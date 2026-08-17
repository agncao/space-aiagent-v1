"""结构化 Planner 与 waiting_user 恢复解析器。"""

from typing import Any, Literal, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from space_aiagent.infrastructure.llm import build_model
from space_aiagent.models.workflow_schemas import PlanDraft, SceneContext, WaitingContext

from .catalog import ActionCatalog


class Planner(Protocol):
    async def plan(self, intent: str, scene_context: SceneContext) -> PlanDraft: ...


class ResumeDecision(BaseModel):
    decision: Literal["open_scene", "create_scene", "provide_arguments", "cancel", "unknown"]
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class StructuredPlanner:
    """无领域工具权限的结构化 Planner。"""

    def __init__(self, catalog: ActionCatalog, model: BaseChatModel | None = None) -> None:
        self._catalog = catalog
        self._model = model or build_model()

    async def plan(self, intent: str, scene_context: SceneContext) -> PlanDraft:
        system = f"""你是航天 GIS 助手的计划生成器，只负责把用户目标拆成 action DAG，不执行工具。
只能使用以下 action：
{self._catalog.planner_context()}

规则：
1. 复合请求必须拆为多个步骤，并用 depends_on 表达顺序。
2. 打开场景后添加实体必须输出 open_scene -> add_entity，不能合并。
3. 批量添加多个实体时，每个实体一个 add_entity 步骤，并串行依赖，避免重复副作用。
4. 用户没有要求打开/创建场景时，不要自行增加场景动作；代码会处理前置条件。
5. args 只写用户明确提供的参数，不得猜测经纬度、TLE、名称或场景。
6. 缺少执行必需参数时写入 missing_arguments，但仍保留原始目标步骤。
7. ref 在本计划内唯一；depends_on 只能引用已有 ref。
8. 只有下游 action 必须消费上游结果时才写 input_bindings；source_ref 必须引用已有 ref，
   pointer 使用 JSON Pointer（例如 /data/entity_id），不得使用“最近结果”。
9. 不生成步骤 ID、状态、执行器、事实或幂等键。
"""
        human = f"当前场景上下文：{scene_context.model_dump(mode='json')}\n用户原始请求：{intent}"
        planner = self._model.with_structured_output(PlanDraft)
        result = await planner.ainvoke([SystemMessage(content=system), HumanMessage(content=human)])
        return PlanDraft.model_validate(result)

    async def resolve_waiting(
        self,
        user_input: str,
        waiting: WaitingContext,
    ) -> ResumeDecision:
        system = """你只把用户对暂停问题的回答转换成结构化决策，不执行动作。
- 用户要打开/选择已有场景：open_scene，args.scene_name 只在用户明确给出时填写。
- 用户要创建/新建场景：create_scene，args.scene_name 只在用户明确命名时填写。
- 用户补充经纬度、TLE、名称等参数：provide_arguments，原样提取到 args。
- 用户明确取消：cancel。
- 无法确认：unknown，不得猜测。
"""
        resolver = self._model.with_structured_output(ResumeDecision)
        result = await resolver.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=(f"暂停上下文：{waiting.model_dump(mode='json')}\n用户回答：{user_input}")),
            ]
        )
        return ResumeDecision.model_validate(result)
