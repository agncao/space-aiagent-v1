"""ResponseStabilizationMiddleware 单测

核心契约：
- _stabilize 必须把渲染后的模板文本写回 AIMessage.content（不是 display_content 字段）
  · AIMessage.content 会被 astream_events 推到 websocket（出口读这个）
  · AIMessage.content 会被 checkpointer 持久化（下一轮 LLM 看到，cross-turn 上下文）
- 原 tool_calls 必须保留（ToolStrategy 结构化输出协议不变）
- 不含 AgentResponse tool_call 的 AIMessage 不应被改动
- awrap_tool_call 构造 tool_record.args 时，缺 scene_name 要从 current_scene_name_var 兜底
"""

import json

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from space_aiagent.bridge import tools_results_var
from space_aiagent.middleware.response_stabilization import ResponseStabilizationMiddleware
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse


def _make_agent_response_message(code: str, summary: str, args: dict | None = None) -> AIMessage:
    """构造一条 AIMessage，其 tool_calls 里携带 AgentResponse"""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "AgentResponse",
                "args": {
                    "status": "info",
                    "code": code,
                    "summary": summary,
                    **({"args": args} if args is not None else {}),
                },
                "id": "call_test",
                "type": "tool_call",
            }
        ],
    )


def test_stabilize_writes_rendered_template_to_ai_message_content():
    """命中模板的 AgentResponse → AIMessage.content 被替换为渲染文本"""
    msg = _make_agent_response_message("NO_SCENE", summary="某 LLM 摘要")
    structured = AgentResponse(status="info", code="NO_SCENE", summary="某 LLM 摘要")
    response = ModelResponse(result=[msg], structured_response=structured)

    mw = ResponseStabilizationMiddleware(agent_name="orchestrator")
    new_response = mw._stabilize(response)

    assert len(new_response.result) == 1
    new_msg = new_response.result[0]
    assert isinstance(new_msg, AIMessage)
    # content 必须被填上渲染文本，而不是空字符串
    assert new_msg.content, "AIMessage.content 不应为空"
    assert "还没有打开的场景" in new_msg.content  # NO_SCENE 模板文本
    assert "某 LLM 摘要" not in new_msg.content  # summary 被忽略，用模板


def test_stabilize_preserves_tool_calls_on_rebuilt_message():
    """重建后的 AIMessage 必须保留原 tool_calls（ToolStrategy 协议）"""
    msg = _make_agent_response_message("NO_SCENE", summary="x")
    structured = AgentResponse(status="info", code="NO_SCENE", summary="x")
    response = ModelResponse(result=[msg], structured_response=structured)

    mw = ResponseStabilizationMiddleware(agent_name="orchestrator")
    new_response = mw._stabilize(response)

    new_msg = new_response.result[0]
    assert new_msg.tool_calls, "tool_calls 不应丢失"
    assert new_msg.tool_calls[0]["name"] == "AgentResponse"
    assert new_msg.tool_calls[0]["id"] == "call_test"


def test_stabilize_fills_template_with_args_and_tool_history():
    """SCENE_CREATED 模板需要 scene_name，args 缺时从 tools_results_var 补"""
    msg = _make_agent_response_message("SCENE_CREATED", summary="x", args=None)
    structured = AgentResponse(status="success", code="SCENE_CREATED", summary="x", args=None)
    response = ModelResponse(result=[msg], structured_response=structured)

    tool_record = {
        "status": "success",
        "code": "SCENE_CREATED",
        "summary": "场景创建成功",
        "args": {},
        "tool_func": "create_scenario",
        "data": {"scene_name": "测试场景"},
    }
    tool_token = tools_results_var.set([tool_record])
    try:
        mw = ResponseStabilizationMiddleware(agent_name="orchestrator")
        new_response = mw._stabilize(response)
    finally:
        tools_results_var.reset(tool_token)

    new_msg = new_response.result[0]
    assert "测试场景" in new_msg.content
    assert "已创建成功" in new_msg.content


def test_stabilize_does_not_modify_message_without_agent_response_tool_call():
    """AIMessage 没有 AgentResponse tool_call → 原样返回，content 不动"""
    plain_msg = AIMessage(content="hi", tool_calls=[])
    response = ModelResponse(result=[plain_msg], structured_response=None)

    mw = ResponseStabilizationMiddleware(agent_name="orchestrator")
    new_response = mw._stabilize(response)

    # 没有改动：返回的 result 里就是原消息
    assert new_response.result[0] is plain_msg or new_response.result[0].content == "hi"


def test_stabilize_passes_through_non_ai_messages():
    """非 AIMessage（如 HumanMessage）原样透传"""
    human = HumanMessage(content="用户输入")
    response = ModelResponse(result=[human], structured_response=None)

    mw = ResponseStabilizationMiddleware(agent_name="orchestrator")
    new_response = mw._stabilize(response)

    assert new_response.result[0] is human


async def test_awrap_tool_call_supplements_scene_name_from_state():
    """awrap_tool_call 构造 tool_record 时，args 无 scene_name → 从 state.current_scene_name 补

    复现 ENTITIES_LIST KeyError 根因：query_entities 返回 args={}, data={entities, count}，
    模板需要 scene_name 但 tool_record 没有，渲染器无法补全。
    """
    # 模拟 query_entities 工具返回
    tool_result = {
        "success": True,
        "code": "ENTITIES_LIST",
        "message": "当前场景中有1个实体",
        "args": {},
        "data": {
            "entities": [{"entity_type": "facility", "entity_name": "酒泉地面站"}],
            "count": 1,
        },
    }
    handle_result = ToolMessage(
        content=json.dumps(tool_result),
        tool_call_id="test_call_1",
        name="query_entities",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return handle_result

    request = ToolCallRequest(
        tool_call={"name": "query_entities", "args": {}, "id": "test_call_1"},
        tool=None,
        state={"current_scene_name": "yy"},
        runtime=None,
    )

    mw = ResponseStabilizationMiddleware(agent_name="scene-agent")

    tool_token = tools_results_var.set([])
    try:
        await mw.awrap_tool_call(request, handler)
    finally:
        records = list(tools_results_var.get() or [])
        tools_results_var.reset(tool_token)

    assert len(records) == 1
    assert records[0]["args"]["scene_name"] == "yy"
