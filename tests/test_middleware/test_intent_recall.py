"""PrimaryAgentMiddleware 意图追踪与自动续接单测

验证中间件对原始意图的捕获、持久化和自动续接行为：
- 职责 2 意图捕获：NO_SCENE 时把原始意图写入 AIMessage.additional_kwargs["pending_intent"]
- 职责 3 自动续接：SCENE_CREATED 且 messages 含 pending_intent 时，替换为 task tool_call
- 边界：空意图、可配置 MET codes、无 pending_intent 时不触发

每个 async 测试函数由 pytest-asyncio 创建独立 task。
"""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from space_aiagent.middleware.primary_agent_middleware import PrimaryAgentMiddleware


def _make_model_request(messages: list) -> ModelRequest:
    """构造 mock ModelRequest，只关心 messages 字段"""
    return ModelRequest(
        model=MagicMock(),
        messages=messages,
    )


def _make_agent_response_model_response(code: str) -> ModelResponse:
    """构造含 AgentResponse tool_call 的 ModelResponse"""
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "AgentResponse",
                        "args": {
                            "status": "info",
                            "code": code,
                            "summary": "...",
                            "suggestions": [],
                        },
                        "id": "call_test",
                        "type": "tool_call",
                    }
                ],
            )
        ],
    )


def _handler_returning(response: ModelResponse) -> Callable[[ModelRequest], Awaitable[ModelResponse]]:
    """构造 async handler，固定返回指定 ModelResponse（供 awrap_model_call await）"""
    return AsyncMock(return_value=response)


def _find_agent_response_ai_message(response: ModelResponse) -> AIMessage:
    """从 ModelResponse 中找到含 AgentResponse tool_call 的 AIMessage"""
    for msg in response.result:
        if (
            isinstance(msg, AIMessage)
            and msg.tool_calls
            and any(tc.get("name") == "AgentResponse" for tc in msg.tool_calls)
        ):
            return msg
    raise AssertionError("未找到含 AgentResponse tool_call 的 AIMessage")


def _find_task_tool_call(response: ModelResponse) -> dict:
    """从 ModelResponse 中提取 task tool_call"""
    for msg in response.result:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "task":
                    return tc
    raise AssertionError("未找到 task tool_call")


# ── 职责 2: 意图捕获 ────────────────────────────────────────


async def test_no_scene_writes_pending_intent():
    """NO_SCENE 时把用户原始意图写入 AIMessage.additional_kwargs['pending_intent']"""
    messages = [HumanMessage("添加祝融号地面车")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware()

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert ai_msg.additional_kwargs["pending_intent"] == "添加祝融号地面车"


async def test_no_scene_skips_when_intent_is_empty():
    """用户输入为空内容时不写入 pending_intent（_extract_original_intent 返回 None）"""
    messages = [HumanMessage("")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware()

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert "pending_intent" not in (ai_msg.additional_kwargs or {})


# ── 职责 3: 自动续接 ────────────────────────────────────────


async def test_auto_continue_on_scene_created():
    """SCENE_CREATED 且 messages 含 pending_intent → 替换为 task 调用，委派 entity-agent"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号地面车"},
        ),
        HumanMessage("好的"),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware()

    handler = _handler_returning(_make_agent_response_model_response("SCENE_CREATED"))
    response = await mw.awrap_model_call(request, handler)

    task_tc = _find_task_tool_call(response)
    assert task_tc["args"]["description"] == "添加祝融号地面车"
    assert task_tc["args"]["subagent_type"] == "entity-agent"

    # 清除历史 messages 中的 pending_intent
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.additional_kwargs:
            assert "pending_intent" not in msg.additional_kwargs


async def test_auto_continue_skips_when_no_pending_intent():
    """messages 中无 pending_intent 时，SCENE_CREATED 不触发自动续接"""
    messages = [HumanMessage("添加祝融号")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware()

    original_response = _make_agent_response_model_response("SCENE_CREATED")
    handler = _handler_returning(original_response)
    response = await mw.awrap_model_call(request, handler)

    # 返回原 response，未被替换为 task
    assert response is original_response


async def test_auto_continue_respects_configurable_codes():
    """precondition_met_codes=frozenset() 时，即使 SCENE_CREATED 也不触发自动续接"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号地面车"},
        ),
        HumanMessage("好的"),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(precondition_met_codes=frozenset())

    original_response = _make_agent_response_model_response("SCENE_CREATED")
    handler = _handler_returning(original_response)
    response = await mw.awrap_model_call(request, handler)

    # 返回原 response，未被替换
    assert response is original_response
    # pending_intent 未被清除
    assert messages[1].additional_kwargs["pending_intent"] == "添加祝融号地面车"


# ── _extract_original_intent 静态方法 ─────────────────────────


def test_extract_original_intent_returns_latest_human():
    """直接返回最新的非空 HumanMessage content（不跳过 ACK）"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(content=""),
        HumanMessage("好的"),
    ]
    assert PrimaryAgentMiddleware._extract_original_intent(messages) == "好的"


def test_extract_original_intent_returns_none_if_no_human():
    """messages 中无 HumanMessage 时返回 None"""
    messages = [AIMessage(content="")]
    assert PrimaryAgentMiddleware._extract_original_intent(messages) is None


def test_extract_original_intent_skips_empty_content():
    """最新的 HumanMessage 内容为空时，继续向前找非空内容"""
    messages = [
        HumanMessage("添加祝融号"),
        HumanMessage(""),
    ]
    assert PrimaryAgentMiddleware._extract_original_intent(messages) == "添加祝融号"
