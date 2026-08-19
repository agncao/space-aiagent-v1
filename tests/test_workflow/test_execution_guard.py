import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from space_aiagent.bridge import bridge_var
from space_aiagent.middleware import worker_tool_validation
from space_aiagent.middleware.worker_tool_validation import WorkerToolValidationMiddleware
from space_aiagent.tools.contracts import get_workflow_tool_contract
from space_aiagent.tools.registry import get_tools
from space_aiagent.workflow.execution_context import (
    StepAlreadyCompletedError,
    StepExecutionContext,
    StepExecutionLimitError,
    step_execution_context_var,
)


def _request(tool_name: str = "open_scenario") -> SimpleNamespace:
    tools = {tool.name: tool for tool in get_tools(["scene_management", "entity_management", "orbit_management"])}
    return SimpleNamespace(
        tool_call={"name": tool_name, "args": {"scene_name": "火箭"}, "id": "call_1"},
        state={},
        tool=tools[tool_name],
    )


def _context(*, scene_opened: bool = True) -> StepExecutionContext:
    return StepExecutionContext(
        run_id="run_1",
        step_id="step_1",
        allowed_tools=frozenset({"query_scenario", "open_scenario", "add_point_entity"}),
        scene_revision=1,
        facts=frozenset({"scene.opened"} if scene_opened else set()),
    )


async def test_successful_completion_tool_is_not_executed_twice(monkeypatch) -> None:
    monkeypatch.setattr(worker_tool_validation, "get_config", lambda: {"configurable": {"thread_id": "t1"}})
    middleware = WorkerToolValidationMiddleware(agent_name="scene-agent")
    handler = AsyncMock(return_value={"success": True, "code": "SCENE_OPENED"})
    bridge_token = bridge_var.set(SimpleNamespace())
    context = _context()
    context_token = step_execution_context_var.set(context)
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
        assert result["success"] is True
        with pytest.raises(StepAlreadyCompletedError):
            await middleware.awrap_tool_call(_request(), handler)
        handler.assert_awaited_once()
        assert context.effects == {"scene.opened"}
    finally:
        step_execution_context_var.reset(context_token)
        bridge_var.reset(bridge_token)


def test_workflow_tool_contract_declares_fact_changes() -> None:
    tools = {tool.name: tool for tool in get_tools(["scene_management"])}

    delete_contract = get_workflow_tool_contract(tools["delete_scene"])

    assert delete_contract.requires == {"scene.opened"}
    assert delete_contract.effects == {"scene.none"}
    assert delete_contract.invalidates == {"scene.opened"}


async def test_same_failed_call_stops_after_two_attempts(monkeypatch) -> None:
    monkeypatch.setattr(worker_tool_validation, "get_config", lambda: {"configurable": {"thread_id": "t1"}})
    middleware = WorkerToolValidationMiddleware(agent_name="scene-agent")
    handler = AsyncMock(return_value={"success": False, "code": "FAILED"})
    bridge_token = bridge_var.set(SimpleNamespace())
    context_token = step_execution_context_var.set(_context())
    try:
        await middleware.awrap_tool_call(_request(), handler)
        await middleware.awrap_tool_call(_request(), handler)
        with pytest.raises(StepExecutionLimitError):
            await middleware.awrap_tool_call(_request(), handler)
        assert handler.await_count == 2
    finally:
        step_execution_context_var.reset(context_token)
        bridge_var.reset(bridge_token)


async def test_worker_tool_allowlist_is_enforced(monkeypatch) -> None:
    monkeypatch.setattr(worker_tool_validation, "get_config", lambda: {"configurable": {"thread_id": "t1"}})
    middleware = WorkerToolValidationMiddleware(agent_name="scene-agent")
    handler = AsyncMock()
    bridge_token = bridge_var.set(SimpleNamespace())
    context_token = step_execution_context_var.set(_context())
    try:
        result = await middleware.awrap_tool_call(_request("delete_scene"), handler)
        assert isinstance(result, ToolMessage)
        handler.assert_not_awaited()
    finally:
        step_execution_context_var.reset(context_token)
        bridge_var.reset(bridge_token)


async def test_missing_fact_returns_worker_requirement(monkeypatch) -> None:
    monkeypatch.setattr(worker_tool_validation, "get_config", lambda: {"configurable": {"thread_id": "t1"}})
    middleware = WorkerToolValidationMiddleware(agent_name="entity-agent")
    handler = AsyncMock()
    bridge_token = bridge_var.set(SimpleNamespace())
    context_token = step_execution_context_var.set(_context(scene_opened=False))
    try:
        result = await middleware.awrap_tool_call(_request("add_point_entity"), handler)
        assert isinstance(result, ToolMessage)
        payload = json.loads(result.content)
        assert payload["code"] == "REQUIREMENT_UNSATISFIED"
        assert payload["requirements"][0]["key"] == "scene.opened"
        handler.assert_not_awaited()
    finally:
        step_execution_context_var.reset(context_token)
        bridge_var.reset(bridge_token)
