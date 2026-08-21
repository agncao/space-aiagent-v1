"""Worker Todo Planner 与最终答复生成器。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, message_to_dict
from pydantic import BaseModel, Field

from space_aiagent.infrastructure.llm import build_model
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.workflow_schemas import (
    PlanDraft,
    SceneContext,
    WorkerRequirement,
    WorkflowRun,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from space_aiagent.workflow.catalog import WorkerCatalog


class Planner(Protocol):
    async def plan(
        self,
        intent: str,
        scene_context: SceneContext,
        *,
        history: list[str] | None = None,
        recovered_tasks: list[str] | None = None,
    ) -> PlanDraft: ...

    async def plan_requirement(
        self,
        requirement: WorkerRequirement,
        *,
        blocked_worker: str,
        blocked_task: str,
        scene_context: SceneContext,
    ) -> PlanDraft:
        """运行时前置需求规划：当某个 Worker 的 Todo 在执行时声明了一个未满足的
        requirement（必须由其他 Worker 提供的前置条件）时，为该 requirement
        生成一个仅包含前置 Todo 的 PlanDraft（source 均为 requirement），
        供 Graph 合并到原计划中、并作为被阻塞 Todo 的依赖。

        典型业务场景：
        用户请求"查询场景中卫星 X 的轨道参数"。satellite_query Worker 执行时
        发现场景里没有实体"卫星 X"，于是声明 requirement：
        key="scene_entity"，value="卫星 X"。Graph 调用本方法（排除
        blocked_worker=satellite_query）后，生成由 scene_manager Worker
        承担的前置 Todo——"在当前场景中加载/打开卫星 X 对应的场景或实体"，
        其 ref 被 satellite_query 原 Todo 的 depends_on 引用，从而先补齐
        场景上下文、再重放原查询。
        """

    async def finalize(self, messages: list[AnyMessage], run: WorkflowRun) -> str: ...


class FinalAnswer(BaseModel):
    content: str = Field(min_length=1)


class StructuredPlanner:
    """只规划 Worker Todo，不选择 action、工具或结构化参数。"""

    def __init__(self, catalog: WorkerCatalog, model: BaseChatModel | None = None) -> None:
        self._catalog = catalog
        self._model = model or build_model()

    async def plan(
        self,
        intent: str,
        scene_context: SceneContext,
        *,
        history: list[str] | None = None,
        recovered_tasks: list[str] | None = None,
    ) -> PlanDraft:
        # 有待恢复步骤时放开规则 8 的"禁止从历史生成新 Todo"，改由下方显式清单提供。
        extra_rule8 = "禁止从历史生成新 Todo。"
        rule8 = (
            "8. 历史仅用于消解指代与省略（如“它”“第二个”指向的具体对象）；"
            "用户本次请求才是唯一任务来源"
            + (extra_rule8 if not recovered_tasks else "。")
        )
        recovered_section = ""
        if recovered_tasks:
            recovered_lines = "\n".join(f"- {task}" for task in recovered_tasks)
            recovered_section = f"""
需要额外完成的步骤（来自上一轮未完成或失败的计划，必须并入本次计划）：
{recovered_lines}
若其中某项与本次请求语义重复，合并为一个 Todo，不要重复生成。
"""
        system = f"""你是航天 GIS 助手的全局任务拆解器。
你只按 Worker 维度把用户目标拆成 Todo DAG，但不执行任务。

可用 Worker：
{self._catalog.planner_context()}
{recovered_section}
规则：
1. 每个 Todo 只写 worker、自然语言 task、source、depends_on 和 required。
2. 所有 Todo 的 source 必须是 user_intent。
3. task 保留用户明确提供的名称、数值和先后语义，但不得提取成 args，不得出现工具名。
4. 复合请求按用户表达的先后顺序拆分；同一 Worker 在不同顺序位置可以出现多次。
5. 不要自行补充用户未提出的前置任务；运行时 requirement 由 Graph 另行规划。
6. ref 唯一；depends_on 只能引用前面的 ref，禁止环和前向引用。
7. 不生成 step_id、状态、执行证据、场景事实或幂等键。
{rule8}
"""
        human = f"用户原始请求：{intent}"
        if history:
            human = "近期会话摘要（newest-last）：\n" + "\n".join(history) + "\n" + human
        return await self._invoke_plan(system, human)

    async def plan_requirement(
        self,
        requirement: WorkerRequirement,
        *,
        blocked_worker: str,
        blocked_task: str,
        scene_context: SceneContext,
    ) -> PlanDraft:
        providers = self._catalog.providers_for(requirement.key, exclude={blocked_worker})
        if not providers:
            raise ValueError(f"没有 Worker 能提供 requirement：{requirement.key}")
        available = self._catalog.planner_context(include=providers)
        system = f"""你是航天 GIS 助手的前置任务拆解器。
当前 Worker Todo 发现了一个必须由其他 Worker 满足的 requirement。
你只生成满足该 requirement 所必需的 Worker Todo DAG，不重复原 Todo，不选择 action、工具或结构化参数。

可用 Worker（已排除当前 Worker）：
{available or "（无）"}

规则：
1. 所有 Todo 的 source 必须是 requirement。
2. task 使用自然语言描述需要达成的前置目标，不写工具名或 args。
3. 只生成前置 Todo，不生成被阻塞的原 Todo。
4. ref 唯一；depends_on 只能引用前面的 ref。
"""
        human = (
            f"当前场景上下文：{scene_context.model_dump(mode='json')}\n"
            f"被阻塞 Worker：{blocked_worker}\n"
            f"被阻塞 Todo：{blocked_task}\n"
            f"requirement：{requirement.model_dump(mode='json')}"
        )
        return await self._invoke_plan(system, human)

    async def finalize(self, messages: list[AnyMessage], run: WorkflowRun) -> str:
        """根据消息链和可信 Run 结果生成直接回答用户问题的最终文本。"""
        system = """你是航天 GIS 工作流的最终答复生成器。
请根据用户原始请求、Worker Todo 执行结果和可信 Run 快照，直接回答用户真正询问的内容。
- 查询类请求必须给出查询到的数量、名称或结果，不要只说“任务已完成”。
- 操作类请求简洁说明实际完成了什么。
- 部分失败或失败时准确指出完成项与失败项。
- 只能使用输入中存在的结果，不得猜测或补造数据。
"""
        transcript = [message_to_dict(message) for message in messages]
        run_payload = {
            "original_intent": run.original_intent,
            "status": run.status.value,
            "steps": [
                {
                    "step_id": step.step_id,
                    "worker": step.worker,
                    "task": step.task,
                    "source": step.source.value,
                    "status": step.status.value,
                    "result": step.result.model_dump(mode="json") if step.result else None,
                    "error": step.error.model_dump(mode="json") if step.error else None,
                }
                for step in run.steps
            ],
        }
        finalizer = self._model.with_structured_output(FinalAnswer)
        result = await finalizer.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(
                    content=(
                        "消息链：\n"
                        + json.dumps(transcript, ensure_ascii=False, default=str)
                        + "\n可信 Run 快照：\n"
                        + json.dumps(run_payload, ensure_ascii=False)
                    )
                ),
            ],
            stream=False,
        )
        return FinalAnswer.model_validate(result).content

    async def _invoke_plan(self, system: str, human: str) -> PlanDraft:
        planner = self._model.with_structured_output(PlanDraft)
        result: Any = await planner.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=human)],
            stream=False,
        )
        return PlanDraft.model_validate(result)
