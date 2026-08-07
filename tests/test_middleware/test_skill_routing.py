"""Skill Flash 预路由与通用工具门禁测试。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from space_aiagent.infrastructure.config import RetryConfig
from space_aiagent.middleware.skill_routing import SkillRouteDecision, SkillRoutingMiddleware
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode
from space_aiagent.infrastructure.skill.catalog import SkillCatalog, SkillDefinition


@tool
def query_scenario() -> str:
    """查询场景。"""
    return "ok"


@tool
def open_scenario() -> str:
    """打开场景。"""
    return "ok"


@tool
def create_scenario() -> str:
    """创建场景。"""
    return "ok"


class FakeRouter:
    def __init__(self, decision: SkillRouteDecision | Exception) -> None:
        self.decision = decision
        self.tasks: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.tasks.append(str(messages[-1].content))
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


def _skill(name: str, tools: set[str]) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        description=f"{name} description",
        path=f"/skills/scene/{name}/SKILL.md",
        content=f"---\nname: {name}\n---\n# {name}",
        allowed_tools=frozenset(tools),
        enforcement="required",
    )


def _middleware(decision: SkillRouteDecision | Exception) -> SkillRoutingMiddleware:
    catalog = SkillCatalog(
        [
            _skill("open-scenario", {"query_scenario", "open_scenario"}),
            _skill("query-scenario", {"query_scenario"}),
        ]
    )
    return SkillRoutingMiddleware(
        agent_name="scene-agent",
        catalog=catalog,
        business_tool_names={"query_scenario", "open_scenario", "create_scenario"},
        router_model=FakeRouter(decision),
        retry_config=RetryConfig(enabled=False),
    )


@pytest.mark.parametrize(
    ("task", "selected"),
    [
        ("打开火箭场景", ["open-scenario"]),
        ("查询火箭场景", ["query-scenario"]),
        ("查询后打开火箭场景", ["open-scenario"]),
    ],
)
async def test_route_activates_selected_skill_for_current_task(task: str, selected: list[str]):
    middleware = _middleware(SkillRouteDecision(decision="matched", selected_skills=selected))
    update = await middleware.abefore_agent({"messages": [HumanMessage(content=task)]}, None)
    assert update == {
        "skill_route_status": "matched",
        "active_skill_names": selected,
        "skill_route_error": None,
    }


async def test_multi_intent_activates_multiple_skills_in_catalog_order():
    middleware = _middleware(
        SkillRouteDecision(decision="matched", selected_skills=["query-scenario", "open-scenario"])
    )
    update = await middleware.abefore_agent({"messages": [HumanMessage(content="先查询，再打开")]}, None)
    assert update["active_skill_names"] == ["open-scenario", "query-scenario"]


async def test_no_match_clears_previous_task_activation():
    middleware = _middleware(SkillRouteDecision(decision="no_match"))
    update = await middleware.abefore_agent(
        {
            "messages": [HumanMessage(content="创建场景")],
            "skill_route_status": "matched",
            "active_skill_names": ["open-scenario"],
        },
        None,
    )
    assert update["skill_route_status"] == "no_match"
    assert update["active_skill_names"] == []


@pytest.mark.parametrize(
    "decision",
    [SkillRouteDecision(decision="ambiguous", reason="无法区分"), RuntimeError("router down")],
)
async def test_ambiguous_or_router_failure_is_fail_closed(decision):
    middleware = _middleware(decision)
    update = await middleware.abefore_agent({"messages": [HumanMessage(content="处理场景")]}, None)
    assert update["skill_route_status"] == "failed"
    assert update["active_skill_names"] == []


def _model_request(state: dict) -> ModelRequest:
    return ModelRequest(
        model=SimpleNamespace(),
        messages=[HumanMessage(content="任务")],
        system_message=SystemMessage(content="base"),
        tools=[query_scenario, open_scenario, create_scenario],
        state=state,
    )


@pytest.mark.parametrize(
    ("state", "expected_tools"),
    [
        (
            {"skill_route_status": "matched", "active_skill_names": ["open-scenario"]},
            {"query_scenario", "open_scenario", "create_scenario"},
        ),
        (
            {"skill_route_status": "matched", "active_skill_names": ["query-scenario"]},
            {"query_scenario", "create_scenario"},
        ),
        ({"skill_route_status": "no_match", "active_skill_names": []}, {"create_scenario"}),
    ],
)
async def test_model_only_sees_tools_authorized_by_active_skills(state: dict, expected_tools: set[str]):
    middleware = _middleware(SkillRouteDecision(decision="no_match"))
    captured = None

    async def handler(request):
        nonlocal captured
        captured = request
        return "ok"

    assert await middleware.awrap_model_call(_model_request(state), handler) == "ok"
    assert {item.name for item in captured.tools} == expected_tools
    if state["skill_route_status"] == "matched":
        assert "已自动激活的 Skills" in captured.system_message.content


async def test_routing_failure_short_circuits_business_model():
    middleware = _middleware(SkillRouteDecision(decision="no_match"))
    handler = AsyncMock()
    result = await middleware.awrap_model_call(
        _model_request({"skill_route_status": "failed", "active_skill_names": []}),
        handler,
    )
    handler.assert_not_awaited()
    assert result.structured_response.code == ResponseCode.SKILL_ROUTING_FAILED


async def test_tool_guard_rejects_bypass_without_calling_handler():
    middleware = _middleware(SkillRouteDecision(decision="no_match"))
    request = SimpleNamespace(
        tool_call={"name": "open_scenario", "args": {"scene_name": "火箭"}, "id": "call_x"},
        state={"skill_route_status": "no_match", "active_skill_names": []},
    )
    handler = AsyncMock()
    result = await middleware.awrap_tool_call(request, handler)
    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert json.loads(result.content)["code"] == ResponseCode.SKILL_ROUTING_FAILED


async def test_tool_guard_allows_active_skill_and_ungoverned_tool():
    middleware = _middleware(SkillRouteDecision(decision="no_match"))
    handler = AsyncMock(return_value="executed")
    active_state = {"skill_route_status": "matched", "active_skill_names": ["query-scenario"]}

    for tool_name in ["query_scenario", "create_scenario"]:
        request = SimpleNamespace(tool_call={"name": tool_name, "args": {}, "id": tool_name}, state=active_state)
        assert await middleware.awrap_tool_call(request, handler) == "executed"
    assert handler.await_count == 2
