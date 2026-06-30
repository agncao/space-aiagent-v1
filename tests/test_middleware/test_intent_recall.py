"""PrimaryAgentMiddleware 意图追踪与自动续接单测

验证中间件对原始意图的捕获、持久化和自动续接行为：
- 职责 2 意图捕获：NO_SCENE 时把原始意图 + subagent_type 写入 AIMessage.additional_kwargs
- 职责 3 自动续接：SCENE_CREATED 且 messages 含 pending_intent 时，替换为 task tool_call
  - 主路径：subagent_type 从 captured_subagent 直接取（流程 2）
  - Fallback：未捕获时调 resolve_subagent_type（流程 1）
- 确认短句过滤：用户输入「好的」/「ok」等时不视为原始意图

辅助函数已从 PrimaryAgentMiddleware 迁出到 infrastructure/utils/message_util.py，
此处通过 import 直接测试纯函数行为。

每个 async 测试函数由 pytest-asyncio 创建独立 task（asyncio_mode = "auto"）。
"""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from space_aiagent.infrastructure.utils import message_util
from space_aiagent.middleware.primary_agent_middleware import PrimaryAgentMiddleware


# ── 公共构造工具 ────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def _mock_build_flash_model():
    """全局 patch build_flash_model，避免 PrimaryAgentMiddleware 构造触发真实 LLM 初始化

    PrimaryAgentMiddleware.__init__ 内调用 build_flash_model() 自建 Flash model，
    单测里无需真实 LLM 客户端，统一替换为 MagicMock。
    """
    with patch(
        "space_aiagent.middleware.primary_agent_middleware.build_flash_model",
        return_value=MagicMock(),
    ) as mocked:
        yield mocked


# ── message_util.extract_last_human_intent 纯函数 ────────────


def test_extract_last_human_intent_returns_latest_non_empty():
    """返回最新的非空 HumanMessage content"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(content=""),
        HumanMessage("添加文昌地面站"),
    ]
    intent, msg = message_util.extract_last_human_intent(messages)
    assert intent == "添加文昌地面站"
    assert msg is messages[2]


def test_extract_last_human_intent_skips_empty_content():
    """最新的 HumanMessage 内容为空时，继续向前找非空内容"""
    messages = [
        HumanMessage("添加祝融号"),
        HumanMessage(""),
    ]
    intent, _ = message_util.extract_last_human_intent(messages)
    assert intent == "添加祝融号"


def test_extract_last_human_intent_returns_none_if_no_human():
    """messages 中无 HumanMessage 时返回 (None, None)"""
    messages = [AIMessage(content="")]
    assert message_util.extract_last_human_intent(messages) == (None, None)


def test_extract_last_human_intent_ignores_confirmation_phrase():
    """传 ignore_messages 后跳过确认短句，回退到之前的实质意图"""
    messages = [
        HumanMessage("添加祝融号地面车"),
        AIMessage(content=""),
        HumanMessage("好的"),
    ]
    intent, _ = message_util.extract_last_human_intent(
        messages, ignore_messages=["好的", "ok"]
    )
    assert intent == "添加祝融号地面车"


def test_extract_last_human_intent_returns_none_if_only_ignored():
    """所有 HumanMessage 都在 ignore 列表时返回 (None, None)"""
    messages = [HumanMessage("好的"), HumanMessage("ok")]
    assert message_util.extract_last_human_intent(
        messages, ignore_messages=["好的", "ok"]
    ) == (None, None)


def test_extract_last_human_intent_default_no_ignore():
    """不传 ignore_messages 时不过滤任何内容"""
    messages = [HumanMessage("好的")]
    assert message_util.extract_last_human_intent(messages) == ("好的", messages[0])


# ── message_util.extract_last_task 纯函数 ────────────────────


def test_extract_last_task_returns_subagent_and_content():
    """messages 含 task tool_call 时返回 (subagent_name, content, tool_call)"""
    task_tc = {
        "name": "task",
        "args": {"description": "添加祝融号", "subagent_type": "entity-agent"},
        "id": "call_1",
        "type": "tool_call",
    }
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(content="委派中", tool_calls=[task_tc]),
    ]
    subagent, content, tc = message_util.extract_last_task(messages)
    assert subagent == "entity-agent"
    assert content == "委派中"
    # langchain 内部会复制 tool_call dict，按值比较
    assert tc == task_tc


def test_extract_last_task_returns_none_if_no_task():
    """messages 无 task tool_call 时返回 (None, None, None)"""
    messages = [HumanMessage("添加祝融号"), AIMessage(content="")]
    assert message_util.extract_last_task(messages) == (None, None, None)


# ── message_util.extract_last_existing_intent 纯函数 ─────────


def test_extract_last_existing_intent_returns_metadata():
    """从 AIMessage.additional_kwargs 提取 pending_intent + pending_subagent"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号地面车", "pending_subagent": "entity-agent"},
        ),
    ]
    intent, subagent, msg = message_util.extract_last_existing_intent(messages)
    assert intent == "添加祝融号地面车"
    assert subagent == "entity-agent"
    assert msg is messages[1]


def test_extract_last_existing_intent_returns_none_if_no_pending():
    """无 pending_intent 时返回 (None, None, None)"""
    messages = [AIMessage(content="", additional_kwargs={})]
    assert message_util.extract_last_existing_intent(messages) == (None, None, None)


# ── 职责 2: 意图捕获 ────────────────────────────────────────


async def test_no_scene_writes_pending_intent():
    """NO_SCENE 时把用户原始意图写入 AIMessage.additional_kwargs['pending_intent']"""
    messages = [HumanMessage("添加祝融号地面车")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert ai_msg.additional_kwargs["pending_intent"] == "添加祝融号地面车"
    # 无 task 历史，pending_subagent 不写入
    assert "pending_subagent" not in ai_msg.additional_kwargs


async def test_no_scene_writes_pending_subagent_when_task_history_exists():
    """messages 含 task tool_call 时，pending_subagent 一并写入"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "task",
                "args": {"description": "添加祝融号", "subagent_type": "entity-agent"},
                "id": "call_task_1",
                "type": "tool_call",
            }],
        ),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert ai_msg.additional_kwargs["pending_intent"] == "添加祝融号"
    assert ai_msg.additional_kwargs["pending_subagent"] == "entity-agent"


async def test_no_scene_skips_when_intent_is_empty():
    """用户输入为空内容且无历史 pending_intent 时不写入 pending_intent"""
    messages = [HumanMessage("")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert "pending_intent" not in (ai_msg.additional_kwargs or {})


async def test_no_scene_skips_when_intent_is_confirmation_phrase():
    """用户本轮输入是确认短句（如「好的」）且无历史 pending_intent 时不写入

    验证 PrimaryAgentMiddleware 把 _CONFIRMATION_PHRASES 通过 ignore_messages
    传给 extract_last_human_intent，恢复「好的」等不视为原始意图的行为。
    """
    messages = [HumanMessage("好的")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert "pending_intent" not in (ai_msg.additional_kwargs or {})


async def test_no_scene_preserves_existing_pending_intent_across_turns():
    """已有 pending_intent 时，新一轮 NO_SCENE 不被本轮确认短句或推进型输入覆盖

    场景：Round1 用户「添加祝融号」→ NO_SCENE → 捕获 pending_intent；
    Round2 用户「创建测试场景」→ 又 NO_SCENE → 应沿用「添加祝融号」而非覆盖。
    """
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号", "pending_subagent": "entity-agent"},
        ),
        HumanMessage("创建测试场景"),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    # 沿用历史 pending_intent，不被「创建测试场景」覆盖
    assert ai_msg.additional_kwargs["pending_intent"] == "添加祝融号"
    assert ai_msg.additional_kwargs["pending_subagent"] == "entity-agent"


# ── 职责 3: 自动续接 ────────────────────────────────────────


async def test_auto_continue_uses_captured_subagent():
    """captured_subagent 非空时 resolve_subagent_type 内部短路，不触发 LLM 分类

    resolve_subagent_type(pending, captured_subagent, model) 在 captured_subagent
    非空时直接 return，不会调 model.with_structured_output()。fixture 注入的
    model 是 MagicMock，若被实际用来调 LLM 会留下 with_structured_output 调用
    记录——此处断言该记录为空。
    """
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={
                "pending_intent": "添加祝融号地面车",
                "pending_subagent": "entity-agent",
            },
        ),
        HumanMessage("好的"),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    handler = _handler_returning(_make_agent_response_model_response("SCENE_CREATED"))
    response = await mw.awrap_model_call(request, handler)

    task_tc = _find_task_tool_call(response)
    assert task_tc["args"]["description"] == "添加祝融号地面车"
    assert task_tc["args"]["subagent_type"] == "entity-agent"
    # captured_subagent 主路径不应触发 LLM 路由分类
    mw._model.with_structured_output.assert_not_called()

    # 清除历史 messages 中的 pending_intent/pending_subagent
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.additional_kwargs:
            assert "pending_intent" not in msg.additional_kwargs
            assert "pending_subagent" not in msg.additional_kwargs


async def test_auto_continue_invokes_resolve_when_no_captured_subagent():
    """无 captured_subagent 时调 resolve_subagent_type，结果用作 subagent_type

    resolve_subagent_type 内部会用 flash_model 调 LLM 分类；此处直接 patch
    该函数，验证它在 captured_subagent=None 时被调用且结果透传到 task tool_call。
    """
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号地面车"},
            # 注意：没有 pending_subagent
        ),
        HumanMessage("好的"),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    resolve_mock = AsyncMock(return_value="entity-agent")
    with patch(
        "space_aiagent.middleware.primary_agent_middleware.resolve_subagent_type",
        new=resolve_mock,
    ):
        handler = _handler_returning(_make_agent_response_model_response("SCENE_CREATED"))
        response = await mw.awrap_model_call(request, handler)

    task_tc = _find_task_tool_call(response)
    assert task_tc["args"]["subagent_type"] == "entity-agent"
    resolve_mock.assert_awaited_once()
    # 调用参数：intent + captured_subagent=None + flash_model
    call_args = resolve_mock.await_args.args
    assert call_args[0] == "添加祝融号地面车"
    assert call_args[1] is None


async def test_auto_continue_skips_when_no_pending_intent():
    """messages 中无 pending_intent 时，SCENE_CREATED 不触发自动续接"""
    messages = [HumanMessage("添加祝融号")]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    original_response = _make_agent_response_model_response("SCENE_CREATED")
    handler = _handler_returning(original_response)
    response = await mw.awrap_model_call(request, handler)

    assert response is original_response


async def test_auto_continue_skips_when_code_not_in_resume_trigger():
    """非 INTENTION_RESUME_TRIGGER_CODES 的成功 code 不触发自动续接

    例如 ENTITY_CREATED（实体创建成功）后即使有 pending_intent 也不应触发。
    常量在 models/response_schema/response_constants.py，不可构造覆盖。
    """
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号地面车"},
        ),
    ]
    request = _make_model_request(messages)
    mw = PrimaryAgentMiddleware(thread_id="test")

    original_response = _make_agent_response_model_response("ENTITY_CREATED")
    handler = _handler_returning(original_response)
    response = await mw.awrap_model_call(request, handler)

    # 返回原 response，未被替换为 task
    assert response is original_response
    # pending_intent 未被清除
    assert messages[1].additional_kwargs["pending_intent"] == "添加祝融号地面车"


# ── TASK_LOOP_GUARD（职责 1，回归保护）─────────────────────


async def test_task_loop_guard_triggers_when_streak_exceeds_threshold():
    """连续 task 调用累计达到阈值时改写为 TASK_LOOP_GUARD 短路响应"""
    from space_aiagent.middleware.primary_agent_middleware import orchestrator_task_streak_var

    messages = [HumanMessage("触发死循环")]
    request = _make_model_request(messages)
    # 阈值设为 1，单次 task 调用即触发
    mw = PrimaryAgentMiddleware(thread_id="test", task_loop_threshold=1)
    orchestrator_task_streak_var.set(0)

    task_response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "task",
                    "args": {"description": "x", "subagent_type": "entity-agent"},
                    "id": "call_task_loop",
                    "type": "tool_call",
                }],
            )
        ],
    )
    handler = _handler_returning(task_response)
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    # 改写后的 AIMessage tool_call name 应为 AgentResponse（携带 TASK_LOOP_GUARD shortcut）
    assert any(tc.get("name") == "AgentResponse" for tc in ai_msg.tool_calls)
