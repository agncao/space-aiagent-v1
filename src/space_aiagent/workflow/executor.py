"""Worker Todo 执行器。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol

from deepagents import create_deep_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langgraph.types import Checkpointer, Command

from space_aiagent.agents.workers import load_workers
from space_aiagent.infrastructure.backend import build_agent_backend
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.response_schema.response_constants import SHORTCUT_RESPONSES
from space_aiagent.models.response_schema.worker_response import ResponseCode, WorkerResponse
from space_aiagent.models.workflow_schemas import (
    PlanStep,
    StepResult,
    WorkerRequirement,
    WorkflowRun,
)
from space_aiagent.tools.contracts import get_workflow_tool_contract
from space_aiagent.workflow.execution_context import (
    StepAlreadyCompletedError,
    StepExecutionContext,
    StepExecutionLimitError,
    StepNoSceneError,
    step_execution_context_var,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

logger = get_logger(__name__)


class StepExecutor(Protocol):
    async def execute(self, run: WorkflowRun, step: PlanStep, execution_id: str) -> StepResult: ...


class AgentStepExecutor:
    """每次只执行一个 Worker Todo；Worker 自行选择 Skill 和工具。"""

    def __init__(
        self,
        checkpointer: Checkpointer | None,
        backend: BackendProtocol | None = None,
    ) -> None:
        self._checkpointer = checkpointer
        self._backend = backend or build_agent_backend()
        self._agents: dict[str, Any] = {}
        self._worker_tool_names: dict[str, frozenset[str]] = {}
        self._worker_tools: dict[str, dict[str, Any]] = {}

    def _get_agent(self, worker_name: str) -> Any:
        if worker_name in self._agents:
            return self._agents[worker_name]
        configs = {item["name"]: item for item in load_workers(self._backend)}
        if worker_name not in configs:
            raise ValueError(f"找不到 Worker: {worker_name}")
        config = configs[worker_name]
        tools = {tool.name: tool for tool in config["tools"]}
        self._worker_tool_names[worker_name] = frozenset(tools)
        self._worker_tools[worker_name] = tools
        worker_prompt = (
            f"{config['system_prompt'].rstrip()}\n\n"
            "## V2 Worker Todo 执行约束\n"
            "你只完成本轮给出的一个 Todo，不规划或委派其他 Worker。"
            "你可以在本 Worker 内选择必要的 Skill 和工具。"
            "若工具返回 REQUIREMENT_UNSATISFIED，必须返回 status=requires 并原样携带 requirements。"
            "工具结果足以回答 Todo 后立即返回 WorkerResponse。"
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
            name=f"v2-{worker_name}",
        )
        self._agents[worker_name] = agent
        return agent

    async def execute(self, run: WorkflowRun, step: PlanStep, execution_id: str) -> StepResult:
        agent = self._get_agent(step.worker)
        tool_names = self._worker_tool_names[step.worker]
        dependency_context = [
            {
                "step_id": dependency.step_id,
                "worker": dependency.worker,
                "task": dependency.task,
                "result": dependency.result.model_dump(mode="json") if dependency.result else None,
            }
            for dependency_id in step.depends_on
            if (dependency := run.step(dependency_id)).result is not None
        ]
        task = (
            f"只执行这个 Worker Todo：{step.task}\n"
            f"Todo 来源：{step.source.value}\n"
            f"原始用户目标：{run.original_intent}\n"
            f"当前场景：{run.scene_context.scene_name or '无'}\n"
            f"直接依赖结果：{dependency_context or '无'}"
        )
        if step.resume_user_input:
            task += f"\n用户补充：{step.resume_user_input}\n补充数据：{step.resume_payload or {}}"

        graph_input: dict[str, Any] | Command = {"messages": [HumanMessage(content=task)]}
        if step.resume_payload and not step.resume_user_input:
            graph_input = Command(resume=step.resume_payload)

        facts = self._facts(run)
        execution_context = StepExecutionContext(
            run_id=run.run_id,
            step_id=step.step_id,
            allowed_tools=tool_names,
            scene_revision=run.scene_context.revision,
            facts=frozenset(facts),
        )
        token = step_execution_context_var.set(execution_context)
        try:
            result = await agent.ainvoke(
                graph_input,
                config={
                    "configurable": {
                        "thread_id": step.agent_thread_id or f"v2:{run.run_id}:{step.step_id}:{step.attempt_count}"
                    },
                    "recursion_limit": 60,
                },
            )
        except StepAlreadyCompletedError as exc:
            contract = get_workflow_tool_contract(self._worker_tools[step.worker][exc.tool_name])
            return StepResult(
                status="success",
                code="DEDUPLICATED_SUCCESS",
                summary=str(exc),
                effects=sorted(contract.effects),
                invalidates=sorted(contract.invalidates),
                evidence={"tool_func": exc.tool_name, "tool_result": exc.result, "deduplicated": True},
            )
        except StepExecutionLimitError as exc:
            return StepResult(
                status="failed",
                code="NO_PROGRESS",
                summary=str(exc),
            )
        except StepNoSceneError as exc:
            shortcut = SHORTCUT_RESPONSES[ResponseCode.NO_SCENE]
            return StepResult(
                status="failed",
                code=shortcut.code.value,
                summary=shortcut.summary,
                evidence={"tool_name": exc.tool_name, "agent_status": "shortcut"},
            )
        finally:
            step_execution_context_var.reset(token)

        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if interrupts:
            values = [getattr(item, "value", item) for item in interrupts]
            step_result = StepResult(
                status="waiting_user",
                code="AGENT_INTERRUPT",
                summary="Todo 需要用户确认后继续。",
                evidence={"waiting_kind": "agent_interrupt", "interrupts": values},
            )
            logger.info(
                f"子智能体{step.worker} Interrupt, 任务:{step.task}",
                thread_id=run.thread_id,
                run_id=run.run_id,
                status=step_result.status,
                evidence=step_result.evidence,
            )
            return step_result

        response = result.get("structured_response") if isinstance(result, dict) else None
        if not isinstance(response, WorkerResponse):
            return StepResult(
                status="failed",
                code="INVALID_STEP_OUTPUT",
                summary="Worker 未返回合法结构化结果。",
            )

        evidence: dict[str, Any] = {
            "agent_status": response.status,
            "tool_call_count": execution_context.tool_call_count,
            "successful_tools": execution_context.successful_tool_names,
        }
        if execution_context.signature_results:
            evidence["tool_result"] = next(reversed(execution_context.signature_results.values()))

        requirements = [
            WorkerRequirement.model_validate(item) for item in execution_context.missing_requirements.values()
        ]
        requirements.extend(response.requirements)
        requirements = list({item.key: item for item in requirements}.values())
        if requirements or response.status == "requires":
            logger.info(
                f"子智能体{step.worker} {response.summary}",
                thread_id=run.thread_id,
                run_id=run.run_id,
                status="waiting_dependency",
            )
            return StepResult(
                status="waiting_dependency",
                code="REQUIREMENT_UNSATISFIED",
                summary=response.summary or "Todo 缺少跨 Worker 前置条件。",
                data=response.data,
                evidence=evidence,
                requirements=requirements,
            )

        code = response.code.value
        if response.code == ResponseCode.MISSING_REQUIRED_INFO or response.status == "confirm":
            step_result = StepResult(
                status="waiting_user",
                code=code,
                summary=response.summary,
                data=response.data,
                evidence={**evidence, "waiting_kind": "missing_arguments"},
            )
            logger.info(
                f"子智能体{step.worker}执行结果:{response.summary}",
                thread_id=run.thread_id,
                run_id=run.run_id,
                status=step_result.status,
                evidence=step_result.evidence,
            )
            return step_result

        if response.status in {"success", "info"}:
            return StepResult(
                status="success",
                code=code,
                summary=response.summary,
                data=response.data,
                effects=sorted(execution_context.effects),
                invalidates=sorted(execution_context.invalidates),
                evidence=evidence,
            )

        return StepResult(
            status="failed",
            code=code,
            summary=response.summary,
            data=response.data,
            evidence=evidence,
        )

    @staticmethod
    def _facts(run: WorkflowRun) -> set[str]:
        facts: set[str] = set()
        if run.scene_context.status == "opened":
            facts.add("scene.opened")
        elif run.scene_context.status == "none":
            facts.add("scene.none")
        for item in run.steps:
            if item.result and item.status.value == "succeeded":
                facts.difference_update(item.result.invalidates)
                facts.update(item.result.effects)
        return facts


def new_execution_id() -> str:
    return f"exec_{uuid.uuid4().hex}"
