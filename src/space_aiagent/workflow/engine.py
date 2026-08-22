"""V2 Worker Todo LangGraph 外层工作流。"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.database import get_db
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.sse_schemas import SSEEventType
from space_aiagent.models.workflow_schemas import (
    PlanDraft,
    ResultRef,
    RunResult,
    RunStatus,
    SceneContext,
    StepError,
    StepResult,
    StepStatus,
    WaitingContext,
    WorkerRequirement,
    WorkerTodoSource,
    WorkflowRun,
    utc_now,
)
from space_aiagent.workflow.catalog import WorkerCatalog
from space_aiagent.workflow.executor import AgentStepExecutor, StepExecutor, new_execution_id
from space_aiagent.workflow.planner import Planner, StructuredPlanner
from space_aiagent.workflow.presentation import waiting_context_snapshot, workflow_run_snapshot
from space_aiagent.workflow.repository import RunRepository, get_run_repository
from space_aiagent.workflow.scheduler import FinalizationGuard, Scheduler
from space_aiagent.workflow.validator import PlanValidationError, PlanValidator

if TYPE_CHECKING:
    from langgraph.types import Checkpointer

logger = get_logger(__name__)
_MAX_DEPENDENCY_DEPTH = 5
# 跨 Run 恢复：可恢复的失败码。NO_SCENE=确定性短路；DEPENDENCY_FAILED=被上游
# 阻塞从未执行的用户意图，链式继承（下轮只看本轮）语义下必须纳入，否则断链。
_RECOVERABLE_ERROR_CODES = frozenset({"NO_SCENE", "DEPENDENCY_FAILED"})
# HITL 中间件要求的 resume 格式是 {"decisions": [...]},且数量须与被拦截的
# 工具调用数一致；前端发送的简化决定值需按下表翻译。
_HITL_APPROVE_VALUES = frozenset({"yes", "approve", "true", "ok", "是", "确认"})
_HITL_REJECT_VALUES = frozenset({"no", "reject", "false", "否"})


def _translate_agent_interrupt_resume(data: dict[str, Any], waiting: WaitingContext) -> dict[str, Any]:
    """把前端简化决定格式翻译成 HITL 中间件要求的 ``decisions`` 列表。

    工具级 interrupt（如场景确认）的 resume 值由 ``_parse_decision`` 宽容
    解析，原样透传即可；仅当等待证据表明中断来自 HITL 中间件
    （interrupts 携带 ``action_requests``）时才做翻译，否则透传。
    """
    if "decisions" in data:
        return data
    decision = data.get("decision", data.get("value"))
    if decision is None:
        return data
    interrupts = (waiting.data or {}).get("interrupts") or []
    action_count = sum(
        len(item.get("action_requests") or [])
        for item in interrupts
        if isinstance(item, dict) and item.get("action_requests") is not None
    )
    if action_count == 0:
        return data
    normalized = str(decision).strip().lower()
    if normalized in _HITL_APPROVE_VALUES:
        decision_type = "approve"
    elif normalized in _HITL_REJECT_VALUES:
        decision_type = "reject"
    else:
        return data
    return {"decisions": [{"type": decision_type} for _ in range(action_count)]}


class WorkflowGraphState(TypedDict, total=False):
    run_id: str
    is_resume: bool
    messages: Annotated[list[AnyMessage], add_messages]
    plan_draft: dict[str, Any]
    decision: Literal["execute", "wait", "finalize"]
    next_step_id: str | None


class WorkflowEngine:
    def __init__(
        self,
        repository: RunRepository,
        catalog: WorkerCatalog,
        planner: Planner,
        executor: StepExecutor,
        checkpointer: Checkpointer | None,
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._planner = planner
        self._validator = PlanValidator(catalog)
        self._scheduler = Scheduler()
        self._finalizer = FinalizationGuard()
        self._executor = executor
        self._graph = self._build_graph(checkpointer)

    def _build_graph(self, checkpointer: Checkpointer | None) -> Any:
        graph = StateGraph(WorkflowGraphState)
        graph.add_node("plan", self._plan_node)
        graph.add_node("validate", self._validate_node)
        graph.add_node("schedule", self._schedule_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("finalize", self._finalize_node)
        graph.add_conditional_edges(START, lambda state: "schedule" if state.get("is_resume") else "plan")
        graph.add_edge("plan", "validate")
        graph.add_edge("validate", "schedule")
        graph.add_conditional_edges(
            "schedule",
            lambda state: state["decision"],
            {"execute": "execute", "wait": END, "finalize": "finalize"},
        )
        graph.add_edge("execute", "schedule")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer)

    async def create_run(
        self,
        *,
        thread_id: str,
        intent: str,
        scene_context: SceneContext,
    ) -> WorkflowRun:
        run = WorkflowRun(
            run_id=f"run_{uuid.uuid4().hex}",
            thread_id=thread_id,
            original_intent=intent,
            scene_context=scene_context,
        )
        bridge = bridge_var.get()
        if bridge is not None and hasattr(bridge, "set_workflow_run"):
            bridge.set_workflow_run(run.run_id)
            bridge.set_workflow_repository(self._repository)
        await self._repository.create_run(run)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run)})
        try:
            logger.debug("开始执行工作流", thread_id=run.thread_id, run_id=run.run_id)
            await self._graph.ainvoke(
                {
                    "run_id": run.run_id,
                    "is_resume": False,
                    "messages": [HumanMessage(content=intent)],
                },
                config={
                    "configurable": {"thread_id": f"workflow:{run.run_id}"},
                    "recursion_limit": 100,
                },
            )
        except Exception as exc:
            logger.exception("workflow.run_failed", run_id=run.run_id)
            await self._fail_run(run.run_id, "WORKFLOW_ERROR", str(exc))
        return await self._resolved_run(run.run_id)

    async def resume_run(
        self,
        run_id: str,
        *,
        user_input: str,
        data: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        run = await self._required_run(run_id)
        if run.status != RunStatus.WAITING_USER or run.waiting_context is None:
            raise ValueError("Run 当前不处于 waiting_user")
        bridge = bridge_var.get()
        if bridge is not None and hasattr(bridge, "set_workflow_repository"):
            bridge.set_workflow_repository(self._repository)
        await self._apply_resume(run, user_input, data or {})
        if run.status == RunStatus.CANCELLED:
            return run
        try:
            await self._graph.ainvoke(
                {
                    "run_id": run.run_id,
                    "is_resume": True,
                    "messages": [HumanMessage(content=user_input)],
                },
                config={
                    "configurable": {"thread_id": f"workflow:{run.run_id}"},
                    "recursion_limit": 100,
                },
            )
        except Exception as exc:
            logger.exception("workflow.resume_failed", run_id=run.run_id)
            await self._fail_run(run.run_id, "WORKFLOW_RESUME_ERROR", str(exc))
        return await self._resolved_run(run.run_id)

    async def cancel_run(self, run_id: str) -> WorkflowRun:
        run = await self._required_run(run_id)
        if run.status in {RunStatus.SUCCEEDED, RunStatus.PARTIALLY_SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return run
        expected = run.revision
        run.status = RunStatus.CANCELLED
        run.waiting_context = None
        for step in run.steps:
            if step.status not in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.BLOCKED,
                StepStatus.CANCELLED,
            }:
                step.status = StepStatus.CANCELLED
                step.updated_at = utc_now()
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run)})
        return run

    async def _plan_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        history = await self._thread_history(run.thread_id, exclude_run_id=run.run_id)
        recovered_tasks = await self._recoverable_tasks(run.thread_id, exclude_run_id=run.run_id)
        logger.debug("workflow._plan_node",thread_id=run.thread_id, run_id=run.run_id,history=history,recovered_tasks=recovered_tasks)
        draft = await self._planner.plan(
            run.original_intent,
            run.scene_context,
            history=history,
            recovered_tasks=recovered_tasks or None,
        )
        logger.debug("workflow._plan_node",thread_id=run.thread_id, run_id=run.run_id,tasks=[todo.task for todo in draft.todos])
        return {
            "plan_draft": draft.model_dump(mode="json"),
            "messages": [self._todo_list_message(draft, WorkerTodoSource.USER_INTENT)],
        }

    async def _thread_history(self, thread_id: str, *, exclude_run_id: str) -> list[str]:
        """从业务事实源组装会话级摘要，供 Planner 消解指代与省略。"""
        recent = await self._repository.list_recent_runs_by_thread(thread_id, limit=5)
        history: list[str] = []
        for run in reversed(recent):
            if run.run_id == exclude_run_id or run.final_result is None:
                continue
            history.append(f"用户：{run.original_intent}")
            history.append(f"助手：{run.final_result.summary}")
        return history

    async def _recoverable_tasks(self, thread_id: str, *, exclude_run_id: str) -> list[str]:
        """筛选上一次 Run 中未完成或可恢复失败的步骤 task，供跨 Run 意图续接。

        只看上一 Run：恢复步骤并入本次计划后即成为本次的正式步骤，链式继承，
        下轮只需扫描本次。筛选：非终态成功/取消，且无错误或错误码可恢复
        （NO_SCENE 短路、DEPENDENCY_FAILED 阻塞）。去重由 Planner 合并语义完成。
        """
        recent = await self._repository.list_recent_runs_by_thread(thread_id, limit=2)
        previous = next(
            (run for run in recent if run.run_id != exclude_run_id),
            None,
        )
        if previous is None:
            return []
        tasks = [
            step.task
            for step in previous.steps
            if step.status not in {StepStatus.SUCCEEDED, StepStatus.CANCELLED}
            and (
                step.error is None
                or step.error.code is None
                or step.error.code in _RECOVERABLE_ERROR_CODES
            )
        ]
        if tasks:
            logger.debug(
                "workflow.recover_unfinished_todos",
                thread_id=thread_id,
                previous_run_id=previous.run_id,
                recovered_tasks=tasks,
            )
        return tasks

    async def _validate_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        logger.debug("workflow.validate_node",thread_id=run.thread_id, run_id=run.run_id)
        draft = PlanDraft.model_validate(state["plan_draft"])
        steps = self._validator.validate(draft, expected_source=WorkerTodoSource.USER_INTENT)
        expected = run.revision
        run.steps = steps
        run.status = RunStatus.RUNNING
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.PLAN_SNAPSHOT, run, {"run": workflow_run_snapshot(run)})
        return {}

    async def _schedule_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        before = run.model_dump(mode="json")
        decision = self._scheduler.decide(run)
        logger.debug(f"workflow._schedule_node, decision={decision.outcome}",thread_id=run.thread_id, run_id=run.run_id)
        if run.model_dump(mode="json") != before:
            expected = run.revision
            await self._repository.save_run(run, expected_revision=expected)
            await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run)})
            if decision.step_id:
                await self._emit(
                    SSEEventType.STEP_UPDATE,
                    run,
                    {"step": run.step(decision.step_id).model_dump(mode="json")},
                )
        return {"decision": decision.outcome, "next_step_id": decision.step_id}

    async def _execute_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        step = run.step(state["next_step_id"] or "")
        expected = run.revision
        step.status = StepStatus.RUNNING
        step.attempt_count += 1
        step.updated_at = utc_now()
        step.agent_thread_id = step.agent_thread_id or (f"v2:{run.run_id}:{step.step_id}:{step.attempt_count}")
        execution_id = new_execution_id()
        await self._repository.save_run(run, expected_revision=expected)

        bridge = bridge_var.get()
        if bridge is not None and hasattr(bridge, "set_workflow_execution"):
            bridge.set_workflow_execution(
                run_id=run.run_id,
                step_id=step.step_id,
                execution_id=execution_id,
                scene_revision=run.scene_context.revision,
                repository=self._repository,
            )
        logger.debug("workflow._execute_node", thread_id=run.thread_id,run_id=run.run_id)
        await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})

        dispatch_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"worker": step.worker, "task": step.task, "step_id": step.step_id},
                    "id": execution_id,
                    "type": "tool_call",
                }
            ],
            additional_kwargs={"message_kind": "worker_dispatch", "step_id": step.step_id},
        )
        logger.debug(f"正将任务分派给子智能体{step.worker}: {dispatch_message}")
        result = await self._executor.execute(run, step.model_copy(deep=True), execution_id)
        result_message = ToolMessage(
            content=result.model_dump_json(),
            tool_call_id=execution_id,
            name="task",
            additional_kwargs={"message_kind": "worker_result", "step_id": step.step_id},
        )
        extra_messages: list[AnyMessage] = [dispatch_message, result_message]

        tool_executions = await self._repository.list_tool_executions(execution_id)
        acknowledged_scene = next(
            (
                item.result
                for item in reversed(tool_executions)
                if item.result and item.result.get("current_scene_name")
            ),
            None,
        )
        evidence_tool_result = result.evidence.get("tool_result")
        if (
            acknowledged_scene is None
            and isinstance(evidence_tool_result, dict)
            and evidence_tool_result.get("current_scene_name")
        ):
            acknowledged_scene = evidence_tool_result

        expected = run.revision
        step.result = result
        step.updated_at = utc_now()
        plan_changed = False
        if result.status == "success":
            step.status = StepStatus.SUCCEEDED
            self._apply_effects(run, result, acknowledged_scene)
        elif result.status == "waiting_user":
            step.status = StepStatus.WAITING_USER
            run.status = RunStatus.WAITING_USER
            waiting_kind = result.evidence.get("waiting_kind", "missing_arguments")
            run.waiting_context = WaitingContext(
                kind=waiting_kind,
                step_id=step.step_id,
                prompt=result.summary,
                result_ref=ResultRef(source_step_id=step.step_id, pointer="/data"),
                data={
                    "code": result.code,
                    **{key: value for key, value in result.evidence.items() if key != "waiting_kind"},
                    **(result.data if isinstance(result.data, dict) else {}),
                },
            )
        elif result.status == "waiting_dependency":
            try:
                requirement_messages = await self._insert_requirement_todos(run, step, result.requirements)
                extra_messages.extend(requirement_messages)
                plan_changed = True
            except (PlanValidationError, ValueError) as exc:
                error = StepError(code="REQUIREMENT_PLANNING_FAILED", message=str(exc))
                step.status = StepStatus.FAILED
                step.error = error
                step.result = StepResult(status="failed", code=error.code, summary=error.message)
        else:
            step.status = StepStatus.FAILED
            step.error = StepError(
                code=result.code,
                message=result.summary,
            )

        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})
        if plan_changed:
            await self._emit(SSEEventType.PLAN_SNAPSHOT, run, {"run": workflow_run_snapshot(run)})
        if bridge is not None and hasattr(bridge, "clear_workflow_execution"):
            bridge.clear_workflow_execution()
        return {"next_step_id": None, "messages": extra_messages}

    async def _insert_requirement_todos(
        self,
        run: WorkflowRun,
        blocked_step: Any,
        requirements: list[WorkerRequirement],
    ) -> list[AIMessage]:
        if not requirements:
            raise ValueError("Worker 声明 waiting_dependency 但未提供 requirement")
        if blocked_step.dependency_depth >= _MAX_DEPENDENCY_DEPTH:
            raise ValueError("requirement Todo 展开超过最大深度")

        insert_at = run.steps.index(blocked_step)
        inherited_dependencies = list(blocked_step.depends_on)
        messages: list[AIMessage] = []
        for requirement in requirements:
            ancestor = blocked_step
            while ancestor.generated_for_step_id is not None:
                if ancestor.requirement_key == requirement.key:
                    raise ValueError(f"requirement 依赖环：{requirement.key}")
                ancestor = run.step(ancestor.generated_for_step_id)
            expansion_key = f"{blocked_step.step_id}:{requirement.key}"
            if expansion_key in blocked_step.dependency_expansion_keys:
                raise ValueError(f"重复 requirement 无进展：{requirement.key}")
            providers = self._catalog.providers_for(requirement.key, exclude={blocked_step.worker})
            if not providers:
                raise ValueError(f"没有 Worker 能提供 requirement：{requirement.key}")
            draft = await self._planner.plan_requirement(
                requirement,
                blocked_worker=blocked_step.worker,
                blocked_task=blocked_step.task,
                scene_context=run.scene_context,
            )
            generated = self._validator.validate(
                draft,
                expected_source=WorkerTodoSource.REQUIREMENT,
                generated_for_step_id=blocked_step.step_id,
                requirement_key=requirement.key,
                dependency_depth=blocked_step.dependency_depth + 1,
                inherited_dependencies=inherited_dependencies,
            )
            unsupported_workers = {todo.worker for todo in generated} - providers
            if unsupported_workers:
                raise ValueError(
                    f"requirement {requirement.key} 被委派给无提供能力的 Worker："
                    + ", ".join(sorted(unsupported_workers))
                )
            run.steps[insert_at:insert_at] = generated
            insert_at += len(generated)
            inherited_dependencies = self._validator.terminal_step_ids(generated)
            blocked_step.dependency_expansion_keys.append(expansion_key)
            messages.append(self._todo_list_message(draft, WorkerTodoSource.REQUIREMENT))

        blocked_step.depends_on = list(dict.fromkeys(inherited_dependencies))
        blocked_step.status = StepStatus.WAITING_DEPENDENCY
        blocked_step.agent_thread_id = None
        blocked_step.resume_payload = None
        blocked_step.resume_user_input = None
        blocked_step.updated_at = utc_now()
        return messages

    async def _finalize_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        expected = run.revision
        logger.info("工作流Todo List是否完成",thread_id=run.thread_id,run_id=run.run_id)
        self._finalizer.finalize(run)
        try:
            logger.info("工作流为本轮次生成总结", thread_id=run.thread_id, run_id=run.run_id)
            final_content = await self._planner.finalize(list(state.get("messages", [])), run)
            if self._is_generic_final_answer(final_content):
                logger.warning("workflow.generic_final_answer_rejected", run_id=run.run_id)
                final_content = run.final_result.summary if run.final_result else "未产生可展示的执行结果。"
            elif run.final_result is not None:
                run.final_result.summary = final_content
        except Exception:
            logger.exception("workflow.final_answer_failed", run_id=run.run_id)
            final_content = run.final_result.summary if run.final_result else "未产生可展示的执行结果。"
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": workflow_run_snapshot(run)})
        return {
            "messages": [
                AIMessage(
                    content=final_content,
                    additional_kwargs={"message_kind": "final_answer", "run_id": run.run_id},
                )
            ]
        }

    async def _apply_resume(self, run: WorkflowRun, user_input: str, data: dict[str, Any]) -> None:
        waiting = run.waiting_context
        if waiting is None:
            raise ValueError("缺少 waiting_context")
        if data.get("decision") == "cancel" or user_input.strip().lower() in {"取消", "cancel"}:
            await self.cancel_run(run.run_id)
            run.status = RunStatus.CANCELLED
            return

        step = run.step(waiting.step_id)
        expected = run.revision
        if waiting.kind == "agent_interrupt":
            step.resume_payload = _translate_agent_interrupt_resume(data, waiting) or {"content": user_input}
            step.resume_user_input = None
        else:
            step.resume_payload = {**waiting.data, **data} if waiting.data else (data or None)
            step.resume_user_input = user_input
            step.agent_thread_id = None
        step.status = StepStatus.PENDING
        step.result = None
        step.error = None
        step.updated_at = utc_now()
        run.status = RunStatus.RUNNING
        run.waiting_context = None
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})

    @staticmethod
    def _apply_effects(
        run: WorkflowRun,
        result: StepResult,
        acknowledged_scene: dict[str, Any] | None,
    ) -> None:
        if "scene.opened" in result.effects:
            scene_name = (
                (acknowledged_scene or {}).get("current_scene_name")
                or result.evidence.get("scene_name")
                or run.scene_context.scene_name
            )
            run.scene_context.status = "opened"
            run.scene_context.scene_name = scene_name
            acknowledged_revision = int((acknowledged_scene or {}).get("scene_revision") or 0)
            run.scene_context.revision = max(run.scene_context.revision + 1, acknowledged_revision)
            run.scene_context.verified_at = utc_now()
        if "scene.none" in result.effects:
            run.scene_context.status = "none"
            run.scene_context.scene_name = None
            run.scene_context.revision += 1
            run.scene_context.verified_at = utc_now()
        elif acknowledged_scene:
            run.scene_context.status = "opened"
            run.scene_context.scene_name = acknowledged_scene.get("current_scene_name") or run.scene_context.scene_name
            run.scene_context.revision = max(
                run.scene_context.revision,
                int(acknowledged_scene.get("scene_revision") or 0),
            )
            run.scene_context.verified_at = utc_now()

    async def _fail_run(self, run_id: str, code: str, message: str) -> None:
        run = await self._required_run(run_id)
        expected = run.revision
        run.status = RunStatus.FAILED
        run.waiting_context = None
        for step in run.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.FAILED
                step.error = StepError(code=code, message=message)
        run.final_result = RunResult(
            status=RunStatus.FAILED,
            summary=message,
            failures=[{"code": code, "message": message}],
        )
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run), "error": message})

    async def _required_run(self, run_id: str) -> WorkflowRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    async def _resolved_run(self, run_id: str) -> WorkflowRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise RuntimeError("WorkflowRun 丢失")
        return run

    async def _emit(self, event: SSEEventType, run: WorkflowRun, payload: dict[str, Any]) -> None:
        await self._repository.append_event(run, event.value, payload)
        bridge = bridge_var.get()
        if bridge is not None:
            if hasattr(bridge, "set_workflow_revision"):
                bridge.set_workflow_revision(run.revision)
            await bridge._emit(event, payload)

    @staticmethod
    def _todo_list_message(draft: PlanDraft, source: WorkerTodoSource) -> AIMessage:
        return AIMessage(
            content=json.dumps(draft.model_dump(mode="json"), ensure_ascii=False),
            additional_kwargs={"message_kind": "worker_todo_list", "source": source.value},
        )

    @staticmethod
    def _run_summary(run: WorkflowRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "status": run.status.value,
            "revision": run.revision,
            "waiting_context": waiting_context_snapshot(run),
        }

    @staticmethod
    def _is_generic_final_answer(content: str) -> bool:
        normalized = content.strip().rstrip("。！!").replace(" ", "")
        return normalized in {"任务已完成", "已完成", "处理完成", "操作完成"}


_engine: WorkflowEngine | None = None


async def get_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        repository = await get_run_repository()
        catalog = WorkerCatalog.from_yaml()
        database = await get_db()
        checkpointer = await database.get_checkpointer()
        planner = StructuredPlanner(catalog)
        executor = AgentStepExecutor(checkpointer)
        _engine = WorkflowEngine(repository, catalog, planner, executor, checkpointer)
    return _engine
