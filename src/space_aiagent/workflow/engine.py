"""V2 LangGraph 外层工作流。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.sse_schemas import SSEEventType

from .executor import StepExecutor, new_execution_id, AgentStepExecutor
from .models import (
    PlanDraft,
    ResultRef,
    RunStatus,
    SceneContext,
    StepError,
    StepResult,
    StepStatus,
    WaitingContext,
    WorkflowRun,
    utc_now,
)
from space_aiagent.workflow.planner import ResumeDecision, StructuredPlanner
from space_aiagent.workflow.presentation import waiting_context_snapshot, workflow_run_snapshot
from space_aiagent.workflow.result_resolver import InputBindingError, ResultResolver
from space_aiagent.workflow.scheduler import FinalizationGuard, Scheduler
from space_aiagent.workflow.validator import PlanValidator
from space_aiagent.infrastructure.database import get_db

if TYPE_CHECKING:
    from langgraph.types import Checkpointer

    from .catalog import ActionCatalog
    from space_aiagent.workflow.repository import RunRepository,get_run_repository

logger = get_logger(__name__)


class WorkflowGraphState(TypedDict, total=False):
    run_id: str
    user_input: str
    is_resume: bool
    plan_draft: dict[str, Any]
    decision: str
    next_step_id: str | None


class WorkflowEngine:
    def __init__(
        self,
        repository: RunRepository,
        catalog: ActionCatalog,
        planner: StructuredPlanner,
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
        self._result_resolver = ResultResolver()
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
        '''
        创建一个会话里的新的一个轮次数据记录
        并启动一个会话里的轮次对话
        '''
        run = WorkflowRun(
            run_id=f"run_{uuid.uuid4().hex}",   #轮次id
            thread_id=thread_id,    # 会话id
            original_intent=intent, # 用户意图
            scene_context=scene_context,    # 当前场景
        )
        bridge = bridge_var.get()
        # 表示会话已经创建，需要更新轮次id
        if bridge is not None and hasattr(bridge, "set_workflow_run"):
            bridge.set_workflow_run(run.run_id)
            bridge.set_workflow_repository(self._repository)
        # 创建一条新的工作流运行记录
        await self._repository.create_run(run)
        # 发送一条轮次事件消息：payload:{run_id,thread_id,RunStatus.PLANNING}
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run)})
        try:
            await self._graph.ainvoke(
                {"run_id": run.run_id, "user_input": intent, "is_resume": False},
                config={"configurable": {"thread_id": f"workflow:{run.run_id}"}, "recursion_limit": 100},
            )
        except Exception as exc:
            logger.exception("workflow.run_failed", run_id=run.run_id)
            await self._fail_run(run.run_id, "WORKFLOW_ERROR", str(exc))

        # 查询当前轮次并返回当前轮次信息
        resolved = await self._repository.get_run(run.run_id)
        if resolved is None:
            raise RuntimeError("WorkflowRun 丢失")
        return resolved

    async def resume_run(
        self,
        run_id: str,
        *,
        user_input: str,
        data: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
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
                {"run_id": run.run_id, "user_input": user_input, "is_resume": True},
                config={"configurable": {"thread_id": f"workflow:{run.run_id}"}, "recursion_limit": 100},
            )
        except Exception as exc:
            logger.exception("workflow.resume_failed", run_id=run.run_id)
            await self._fail_run(run.run_id, "WORKFLOW_RESUME_ERROR", str(exc))
        resolved = await self._repository.get_run(run.run_id)
        if resolved is None:
            raise RuntimeError("WorkflowRun 丢失")
        return resolved

    async def cancel_run(self, run_id: str) -> WorkflowRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
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
                StepStatus.SKIPPED,
                StepStatus.CANCELLED,
            }:
                step.status = StepStatus.CANCELLED
                step.updated_at = utc_now()
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run)})
        return run

    async def _plan_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        draft = await self._planner.plan(run.original_intent, run.scene_context)
        return {"plan_draft": draft.model_dump(mode="json")}

    async def _validate_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        draft = PlanDraft.model_validate(state["plan_draft"])
        steps = self._validator.validate(draft, run.scene_context)
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
        try:
            resolved_args = self._result_resolver.resolve_args(run, step)
        except InputBindingError as exc:
            expected = run.revision
            error = StepError(code="INPUT_BINDING_ERROR", message=str(exc), retryable=False)
            step.status = StepStatus.FAILED
            step.error = error
            step.result = StepResult(
                status="failed",
                code=error.code,
                summary=error.message,
                error=error,
            )
            step.updated_at = utc_now()
            await self._repository.save_run(run, expected_revision=expected)
            await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})
            return {"next_step_id": None}

        execution_step = step.model_copy(deep=True, update={"args": resolved_args})
        expected = run.revision
        step.status = StepStatus.RUNNING
        step.attempt_count += 1
        step.updated_at = utc_now()
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
        await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})

        result = await self._executor.execute(run, execution_step, execution_id)
        tool_executions = await self._repository.list_tool_executions(execution_id)
        acknowledged_scene = next(
            (
                item.result
                for item in reversed(tool_executions)
                if item.result and (item.result.get("current_scene_name") or item.result.get("current_scene_id"))
            ),
            None,
        )
        evidence_tool_result = result.evidence.get("tool_result")
        if (
            acknowledged_scene is None
            and isinstance(evidence_tool_result, dict)
            and (evidence_tool_result.get("current_scene_name") or evidence_tool_result.get("current_scene_id"))
        ):
            acknowledged_scene = evidence_tool_result
        expected = run.revision
        step.result = result
        step.updated_at = utc_now()
        if result.status == "success":
            step.status = StepStatus.SUCCEEDED
            if "scene.opened" in result.effects:
                scene_name = (
                    (acknowledged_scene or {}).get("current_scene_name")
                    or result.evidence.get("scene_name")
                    or execution_step.args.get("scene_name")
                )
                run.scene_context.status = "opened"
                run.scene_context.scene_name = scene_name or run.scene_context.scene_name
                run.scene_context.scene_id = (acknowledged_scene or {}).get(
                    "current_scene_id"
                ) or run.scene_context.scene_id
                acknowledged_revision = int((acknowledged_scene or {}).get("scene_revision") or 0)
                run.scene_context.revision = max(run.scene_context.revision + 1, acknowledged_revision)
                run.scene_context.verified_at = utc_now()
            if "scene.none" in result.effects:
                run.scene_context.status = "none"
                run.scene_context.scene_id = None
                run.scene_context.scene_name = None
                run.scene_context.revision += 1
                run.scene_context.verified_at = utc_now()
            elif acknowledged_scene:
                run.scene_context.scene_id = acknowledged_scene.get("current_scene_id") or run.scene_context.scene_id
                run.scene_context.scene_name = (
                    acknowledged_scene.get("current_scene_name") or run.scene_context.scene_name
                )
                run.scene_context.revision = max(
                    run.scene_context.revision,
                    int(acknowledged_scene.get("scene_revision") or 0),
                )
                run.scene_context.verified_at = utc_now()
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
                },
            )
        else:
            step.status = StepStatus.FAILED
            step.error = result.error or StepError(code=result.code, message=result.summary, retryable=result.retryable)
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})
        if bridge is not None and hasattr(bridge, "clear_workflow_execution"):
            bridge.clear_workflow_execution()
        return {"next_step_id": None}

    async def _finalize_node(self, state: WorkflowGraphState) -> dict[str, Any]:
        run = await self._required_run(state["run_id"])
        expected = run.revision
        self._finalizer.finalize(run)
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": workflow_run_snapshot(run)})
        return {}

    async def _apply_resume(self, run: WorkflowRun, user_input: str, data: dict[str, Any]) -> None:
        waiting = run.waiting_context
        if waiting is None:
            raise ValueError("缺少 waiting_context")
        step = run.step(waiting.step_id)
        decision = await self._resume_decision(user_input, waiting, data)
        if decision.decision == "cancel":
            await self.cancel_run(run.run_id)
            run.status = RunStatus.CANCELLED
            return
        if waiting.kind == "missing_precondition":
            if decision.decision not in {"open_scene", "create_scene"}:
                raise ValueError("请明确选择打开已有场景或创建场景")
            definition = self._catalog.get(decision.decision)
            step.action = definition.name
            step.title = "打开已有场景" if decision.decision == "open_scene" else "创建新场景"
            step.args = decision.args
            step.executor = definition.executor
            step.allowed_tools = definition.allowed_tools
            step.requires = definition.requires
            step.provides = definition.provides
            step.side_effect = definition.side_effect
        elif waiting.kind == "scene_selection":
            scene_name = data.get("scene_name") or decision.args.get("scene_name") or user_input.strip()
            if not scene_name:
                raise ValueError("缺少选中的场景名")
            candidates = step.result.data if step.result and isinstance(step.result.data, list) else []
            candidate_names = {
                item.get("scene_name") for item in candidates if isinstance(item, dict) and item.get("scene_name")
            }
            if scene_name not in candidate_names:
                raise ValueError("选中的场景不在候选列表中")
            step.args["scene_name"] = scene_name
        elif waiting.kind == "agent_interrupt":
            step.args["_agent_resume"] = data or {"content": user_input}
        else:
            if decision.decision != "provide_arguments" and not data:
                raise ValueError("未识别到待补充参数")
            step.args.update(data.get("args", data) or decision.args)
            step.missing_arguments = []

        expected = run.revision
        step.status = StepStatus.PENDING
        step.result = None
        step.error = None
        step.updated_at = utc_now()
        run.status = RunStatus.RUNNING
        run.waiting_context = None
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.STEP_UPDATE, run, {"step": step.model_dump(mode="json")})

    async def _resume_decision(
        self,
        user_input: str,
        waiting: WaitingContext,
        data: dict[str, Any],
    ) -> ResumeDecision:
        explicit = data.get("decision") or data.get("action")
        if explicit in {"open_scene", "create_scene", "provide_arguments", "cancel", "unknown"}:
            args = data.get("args", {})
            if data.get("scene_name"):
                args = {**args, "scene_name": data["scene_name"]}
            return ResumeDecision(decision=explicit, args=args)
        if waiting.kind == "scene_selection" and (data.get("scene_name") or user_input.strip()):
            return ResumeDecision(
                decision="open_scene",
                args={"scene_name": data.get("scene_name") or user_input.strip()},
            )
        return await self._planner.resolve_waiting(user_input, waiting)

    async def _fail_run(self, run_id: str, code: str, message: str) -> None:
        run = await self._required_run(run_id)
        expected = run.revision
        run.status = RunStatus.FAILED
        run.waiting_context = None
        for step in run.steps:
            if step.status in {StepStatus.RUNNING, StepStatus.WAITING_TOOL}:
                step.status = StepStatus.FAILED
                step.error = StepError(code=code, message=message)
        await self._repository.save_run(run, expected_revision=expected)
        await self._emit(SSEEventType.RUN_UPDATE, run, {"run": self._run_summary(run), "error": message})

    async def _required_run(self, run_id: str) -> WorkflowRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    async def _emit(self, event: SSEEventType, run: WorkflowRun, payload: dict[str, Any]) -> None:
        await self._repository.append_event(run, event.value, payload)
        bridge = bridge_var.get()
        if bridge is not None:
            if hasattr(bridge, "set_workflow_revision"):
                bridge.set_workflow_revision(run.revision)
            await bridge._emit(event, payload)

    @staticmethod
    def _run_summary(run: WorkflowRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "status": run.status.value,
            "revision": run.revision,
            "waiting_context": waiting_context_snapshot(run),
        }


async def get_engine() -> WorkflowEngine:
    """获取全局 WorkflowEngine 单例，延迟初始化。

    Returns:
        WorkflowEngine: 全局唯一的引擎实例。
    """
    global _engine
    if _engine is None:
        # 得到一个 RunRepository（持久化运行记录）
        repository = await get_run_repository()
        # 从 YAML 加载动作定义
        catalog = ActionCatalog.from_yaml()
        database = await get_db()
        # checkpointer 数据库实例
        checkpointer = await database.get_checkpointer()
        # 初始化规划起
        planner = StructuredPlanner(catalog)
        # 初始化步骤执行器
        executor = AgentStepExecutor(catalog, checkpointer)
        _engine = WorkflowEngine(repository, catalog, planner, executor, checkpointer)
    return _engine