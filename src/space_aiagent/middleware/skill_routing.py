"""通用 Skill 预路由、正文注入与工具门禁。"""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import BaseModel, Field, model_validator
from tenacity import AsyncRetrying, RetryError, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from space_aiagent.infrastructure.config import RetryConfig
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.infrastructure.utils import message_util
from space_aiagent.middleware.retry import _build_retryable_llm_errors, _make_before_sleep
from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode
from space_aiagent.infrastructure.skill.catalog import SkillCatalog, SkillCatalogError, SkillDefinition

logger = get_logger(__name__)

SkillRouteStatus = Literal["matched", "no_match", "failed"]


class SkillRouteDecision(BaseModel):
    """Flash 路由器的唯一合法输出。"""

    decision: Literal["matched", "no_match", "ambiguous"]
    selected_skills: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def _validate_selection(self) -> "SkillRouteDecision":
        if self.decision == "matched" and not self.selected_skills:
            raise ValueError("matched 必须选择至少一个 Skill")
        if self.decision != "matched" and self.selected_skills:
            raise ValueError("no_match/ambiguous 不得携带 selected_skills")
        return self


class SkillRoutingState(AgentState):
    """只在当前子 Agent 内可见的 Skill 路由状态。"""

    skill_route_status: NotRequired[Annotated[SkillRouteStatus, PrivateStateAttr]]
    active_skill_names: NotRequired[Annotated[list[str], PrivateStateAttr]]
    skill_route_error: NotRequired[Annotated[str | None, PrivateStateAttr]]

# 提取最后一次human message
# 子智能体的human message 就是主智能体给它的任务描述
def _extract_last_human_content(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        if isinstance(message.content, str):
            content = message.content.strip()
        else:
            content = " ".join(
                str(block.get("text", ""))
                for block in message.content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
        return content or None
    return None


def _append_system_text(system_message: SystemMessage | None, text: str) -> SystemMessage:
    if system_message is None:
        return SystemMessage(content=text)
    if isinstance(system_message.content, str):
        return SystemMessage(content=f"{system_message.content}\n\n{text}")
    return SystemMessage(content=[*system_message.content, {"type": "text", "text": text}])


class SkillRoutingMiddleware(AgentMiddleware[SkillRoutingState]):
    """在业务推理前自动激活 Skill，并对受管工具 fail-closed。"""

    state_schema = SkillRoutingState

    def __init__(
        self,
        *,
        agent_name: str,
        catalog: SkillCatalog,
        business_tool_names: set[str],
        router_model: BaseChatModel,
        retry_config: RetryConfig,
    ) -> None:
        self._agent_name = agent_name
        self._catalog = catalog
        self._business_tool_names = frozenset(business_tool_names)
        self._router = router_model.with_structured_output(SkillRouteDecision, method="function_calling")
        self._retry_config = retry_config
        self._retryable_errors = _build_retryable_llm_errors(retry_config.llm.retry_on_parse_error)

    def _router_messages(self, task: str) -> list[SystemMessage | HumanMessage]:
        skill_lines = "\n".join(f"- {skill.name}: {skill.description}" for skill in self._catalog.skills)
        prompt = (
            "你是 Skill 路由器。只根据用户任务与 description 判断，不根据工具名猜测。\n"
            "若一个或多个 Skill 明确匹配，decision=matched 并返回全部匹配名称；"
            "没有匹配则 decision=no_match；无法可靠区分才返回 ambiguous。\n"
            "selected_skills 只能使用清单中的精确名称。\n\n"
            f"可用 Skills：\n{skill_lines or '（无）'}"
        )
        return [SystemMessage(content=prompt), HumanMessage(content=task)]

    async def _route(self, task: str) -> SkillRouteDecision:
        messages = self._router_messages(task)
        if not self._retry_config.enabled:
            return await self._router.ainvoke(messages)

        with optional_span("skill.route.retry") as span:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(self._retry_config.llm.max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._retry_config.llm.base_delay,
                    max=self._retry_config.llm.max_delay,
                ),
                retry=retry_if_exception_type(self._retryable_errors),
                before_sleep=_make_before_sleep(span),
            )
            try:
                return await retrying(self._router.ainvoke, messages)
            except RetryError as exc:
                raise RuntimeError("Skill 路由重试耗尽") from exc

    async def abefore_agent(self, state: SkillRoutingState, runtime: Any) -> dict[str, Any]:
        """每次子 Agent 调用都覆盖私有路由状态，禁止跨任务复用。"""
        task = _extract_last_human_content(state.get("messages", []))
        if not task:
            return {
                "skill_route_status": "failed",
                "active_skill_names": [],
                "skill_route_error": "缺少可路由的用户任务",
            }

        with optional_span("skill.route", **{"subagent.name": self._agent_name}) as span:
            set_span_io(span, input={"task": task, "skills": sorted(self._catalog.names)})
            try:
                decision = await self._route(task)
                set_span_io(span, output=decision.model_dump())
                if decision.decision == "ambiguous":
                    raise SkillCatalogError(f"Skill 意图不明确：{decision.reason or 'router returned ambiguous'}")
                if decision.decision == "no_match":
                    logger.info("skill.matched", agent=self._agent_name, status="no_match")
                    return {
                        "skill_route_status": "no_match",
                        "active_skill_names": [],
                        "skill_route_error": None,
                    }

                selected = self._catalog.select(decision.selected_skills)
                names = [skill.name for skill in selected]
                for skill in selected:
                    logger.info(
                        "skill.matched",
                        agent=self._agent_name,
                        skill_name=skill.name,
                        trigger_source="automatic",
                    )
                    logger.info(
                        "skill.loaded",
                        agent=self._agent_name,
                        skill_name=skill.name,
                        path=skill.path,
                        trigger_source="automatic",
                    )
                return {
                    "skill_route_status": "matched",
                    "active_skill_names": names,
                    "skill_route_error": None,
                }
            except Exception as exc:
                span.set_attribute("skill.route.failed", True)
                span.set_attribute("skill.route.error", type(exc).__name__)
                logger.exception("skill.load_failed", agent=self._agent_name, error=type(exc).__name__)
                return {
                    "skill_route_status": "failed",
                    "active_skill_names": [],
                    "skill_route_error": str(exc),
                }

    def _active_skills(self, state: SkillRoutingState) -> list[SkillDefinition]:
        if state.get("skill_route_status") != "matched":
            return []
        return self._catalog.select(list(state.get("active_skill_names", [])))

    def _allowed_business_tools(self, state: SkillRoutingState) -> frozenset[str]:
        ungoverned = self._business_tool_names - self._catalog.governed_tools
        active_allowed = frozenset(tool for skill in self._active_skills(state) for tool in skill.allowed_tools)
        return frozenset(ungoverned | active_allowed)

    @staticmethod
    def _skill_section(skills: list[SkillDefinition]) -> str:
        sections = [
            "## 已自动激活的 Skills",
            "以下 SKILL.md 已由后端根据当前任务加载。必须严格执行；无需再次读取这些主文件。",
        ]
        for skill in skills:
            sections.extend([f'\n<skill name="{skill.name}" path="{skill.path}">', skill.content, "</skill>"])
        return "\n".join(sections)

    @staticmethod
    def _failure_response() -> ModelResponse:
        shortcut = response_constants.SHORTCUT_RESPONSES[ResponseCode.SKILL_ROUTING_FAILED]
        display = response_util.render(shortcut)
        return message_util.build_primary_agent_response(display, shortcut, "call_skill_routing_failed")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        status = request.state.get("skill_route_status")
        if status == "failed" or status not in {"matched", "no_match"}:
            return self._failure_response()

        allowed_business_tools = self._allowed_business_tools(request.state)
        filtered_tools = [
            tool
            for tool in request.tools
            if tool.name not in self._business_tool_names or tool.name in allowed_business_tools
        ]
        modified = request.override(tools=filtered_tools)
        active_skills = self._active_skills(request.state)
        if active_skills:
            modified = modified.override(
                system_message=_append_system_text(request.system_message, self._skill_section(active_skills))
            )
        return await handler(modified)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        tool_name = request.tool_call.get("name", "")
        tool_call_id = request.tool_call.get("id", "")
        if tool_name == "read_file":
            result = await handler(request)
            file_path = str(request.tool_call.get("args", {}).get("file_path", ""))
            if file_path.endswith("/SKILL.md"):
                logger.info(
                    "skill.loaded",
                    agent=self._agent_name,
                    path=file_path,
                    trigger_source="model",
                )
            return result

        if tool_name in self._business_tool_names:
            allowed = self._allowed_business_tools(request.state)
            if request.state.get("skill_route_status") == "failed" or tool_name not in allowed:
                logger.warning(
                    "skill.bypassed",
                    agent=self._agent_name,
                    tool_name=tool_name,
                    active_skills=request.state.get("active_skill_names", []),
                )
                return ToolMessage(
                    content=json.dumps(
                        {
                            "success": False,
                            "code": ResponseCode.SKILL_ROUTING_FAILED,
                            "message": "该工具受 Skill 管理，当前任务未激活授权 Skill，已拒绝执行",
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
        return await handler(request)
