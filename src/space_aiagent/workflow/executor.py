"""复用 DeepAgents Worker 的 V2 步骤执行器。"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langgraph.types import Checkpointer, Command

from space_aiagent.agents.workers import load_workers
from space_aiagent.infrastructure.backend import build_agent_backend
from space_aiagent.models.response_schema.worker_response import ResponseCode, WorkerResponse
from space_aiagent.models.workflow_schemas import PlanStep, StepError, StepResult, WorkflowRun

from space_aiagent.workflow.catalog import ActionCatalog
from space_aiagent.workflow.execution_context import (
    StepAlreadyCompletedError,
    StepExecutionContext,
    StepExecutionLimitError,
    step_execution_context_var,
)


class StepExecutor(Protocol):
    async def execute(self, run: WorkflowRun, step: PlanStep, execution_id: str) -> StepResult: ...


class AgentStepExecutor:
    """每次只给 Worker 一个 Action，外层顺序不再交给模型。"""

    def __init__(
        self,
        catalog: ActionCatalog,
        checkpointer: Checkpointer | None,
        backend: BackendProtocol | None = None,
    ) -> None:
        self._catalog = catalog
        self._checkpointer = checkpointer
        self._backend = backend or build_agent_backend()
        self._agents: dict[str, Any] = {}

    def _get_agent(self, executor_name: str) -> Any:
        if executor_name in self._agents:
            return self._agents[executor_name]
        configs = {item["name"]: item for item in load_workers(self._backend)}
        if executor_name not in configs:
            raise ValueError(f"找不到步骤执行器: {executor_name}")
        config = configs[executor_name]
        worker_tool_names = {tool.name for tool in config["tools"]}
        for action in self._catalog.definitions():
            if action.executor != executor_name:
                continue
            missing_tools = set(action.allowed_tools) - worker_tool_names
            if missing_tools:
                raise ValueError(f"action {action.name} 引用了 {executor_name} 未绑定的工具: {sorted(missing_tools)}")
        worker_prompt = (
            f"{config['system_prompt'].rstrip()}\n\n"
            "## V2 单步骤执行约束\n"
            "你只执行本轮给出的一个 action。不得规划或委派后续 action；工具结果返回后立即生成 WorkerResponse。"
        )
        agent = create_deep_agent(
            model=config["model"],
            tools=config["tools"],
            system_prompt=worker_prompt,
            middleware=config.get("middleware", []),
            skills=config.get("skills"),
            interrupt_on=config.get("interrupt_on"),
            backend=self._backend,
            response_format=ToolStrategy(WorkerResponse),
            checkpointer=self._checkpointer,
            name=f"v2-{executor_name}",
        )
        self._agents[executor_name] = agent
        return agent

    async def execute(self, run: WorkflowRun, step: PlanStep, execution_id: str) -> StepResult:
        if step.executor == "system":
            raise ValueError("system step 不应交给 AgentStepExecutor")
        action = self._catalog.get(step.action)
        agent = self._get_agent(step.executor)
        task = (
            f"只执行这个步骤：{step.title}\n"
            f"action: {step.action}\n"
            f"args: {step.args}\n"
            f"原始用户目标（仅供参数语义参考，不得执行其他步骤）：{run.original_intent}\n"
            f"当前场景：{run.scene_context.scene_name or '无'}\n"
            f"允许的业务工具：{', '.join(step.allowed_tools) or '无'}"
        )
        graph_input: dict[str, Any] | Command = {"messages": [HumanMessage(content=task)]}
        if resume_payload := step.args.pop("_agent_resume", None):
            graph_input = Command(resume=resume_payload)

        step_execute_context = StepExecutionContext(
            run_id=run.run_id,
            step_id=step.step_id,
            execution_id=execution_id,
            allowed_tools=frozenset(step.allowed_tools),
            completion_tools=frozenset(action.completion_tools),
            scene_revision=run.scene_context.revision,
            scene_opened=run.scene_context.status == "opened",
        )
        step_execute_context_token = step_execution_context_var.set(step_execute_context)
        try:
            result = await agent.ainvoke(
                graph_input,
                config={
                    "configurable": {"thread_id": f"v2:{run.run_id}:{step.step_id}"},
                    "recursion_limit": 60,
                },
            )
        except StepAlreadyCompletedError as exc:
            return StepResult(
                status="success",
                code="DEDUPLICATED_SUCCESS",
                summary=str(exc),
                effects=action.provides,
                evidence={"tool_func": exc.tool_name, "tool_result": exc.result, "deduplicated": True},
            )
        except StepExecutionLimitError as exc:
            return StepResult(
                status="failed",
                code="NO_PROGRESS",
                summary=str(exc),
                error=StepError(code="NO_PROGRESS", message=str(exc), retryable=False),
            )
        finally:
            step_execution_context_var.reset(step_execute_context_token)

        # 1. config/workers.yaml 配置了 interrupt_on 会在此中断
        # 2. 在此处 执行器捕获
        # 3. 然后在 engine.py _execute_node方法：
        #         waiting_kind = result.evidence.get("waiting_kind", "missing_arguments")
        #         run.waiting_context = WaitingContext(
        #             kind=waiting_kind,  # → "agent_interrupt"
        #             ...
        #         )
        # 4. 恢复时消费：_apply_resume 方法:
        #         elif waiting.kind == "agent_interrupt":
        #         step.args["_agent_resume"] = data or {"content": user_input}
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if interrupts:
            values = [getattr(item, "value", item) for item in interrupts]
            return StepResult(
                status="waiting_user",
                code="AGENT_INTERRUPT",
                summary="步骤需要用户确认后继续。",
                evidence={"waiting_kind": "agent_interrupt", "interrupts": values},
            )

        response = result.get("structured_response") if isinstance(result, dict) else None
        if not isinstance(response, WorkerResponse):
            return StepResult(
                status="failed",
                code="INVALID_STEP_OUTPUT",
                summary="Worker 未返回合法结构化结果。",
                error=StepError(code="INVALID_STEP_OUTPUT", message="缺少 WorkerResponse"),
            )

        code = response.code.value
        evidence: dict[str, Any] = {
            "agent_status": response.status,
            "tool_call_count": step_execute_context.tool_call_count,
        }
        if step_execute_context.signature_results:
            evidence["tool_result"] = next(reversed(step_execute_context.signature_results.values()))

        if response.code == ResponseCode.NO_SCENE:
            return StepResult(
                status="waiting_user",
                code=code,
                summary=response.summary,
                data=response.data,
                evidence={**evidence, "waiting_kind": "missing_precondition"},
            )
        if response.code == ResponseCode.MISSING_REQUIRED_INFO or response.status == "confirm":
            return StepResult(
                status="waiting_user",
                code=code,
                summary=response.summary,
                data=response.data,
                evidence={**evidence, "waiting_kind": "missing_arguments"},
            )
        if step.action == "open_scene" and response.code == ResponseCode.SCENE_QUERIED:
            candidates = response.data or []
            if len(candidates) > 1:
                return StepResult(
                    status="waiting_user",
                    code=code,
                    summary=response.summary,
                    data=candidates,
                    evidence={**evidence, "waiting_kind": "scene_selection"},
                )
            return StepResult(
                status="failed",
                code="OPEN_SCENE_INCOMPLETE",
                summary="场景查询完成但未打开唯一场景。",
                data=candidates,
                error=StepError(code="OPEN_SCENE_INCOMPLETE", message="Worker 未完成 open_scene action"),
                evidence=evidence,
            )

        if code in action.completion_codes:
            return StepResult(
                status="success",
                code=code,
                summary=response.summary,
                data=response.data,
                effects=action.provides,
                evidence=evidence,
            )
        if response.status == "success" and not action.completion_codes:
            return StepResult(
                status="success",
                code=code,
                summary=response.summary,
                data=response.data,
                effects=action.provides,
                evidence=evidence,
            )
        return StepResult(
            status="failed",
            code=code,
            summary=response.summary,
            data=response.data,
            error=StepError(code=code, message=response.summary, retryable=False),
            evidence=evidence,
        )


def new_execution_id() -> str:
    return f"exec_{uuid.uuid4().hex}"
