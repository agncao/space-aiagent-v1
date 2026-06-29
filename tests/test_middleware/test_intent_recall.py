"""PrimaryAgentMiddleware 意图追踪与自动续接单测

验证中间件对原始意图的捕获、持久化和自动续接行为：
- 职责 2 意图捕获：NO_SCENE 时把原始意图 + subagent_type 写入 AIMessage.additional_kwargs
- 职责 3 自动续接：SCENE_CREATED 且 messages 含 pending_intent 时，替换为 task tool_call
  - 主路径：subagent_type 从 captured_subagent 直接取（流程 2）
  - Fallback：未捕获时调 LLM 分类（流程 1）
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


def _make_middleware(
    subagent_summaries: list[dict[str, str]] | None = None,
    model: MagicMock | None = None,
    precondition_met_codes=None,
) -> PrimaryAgentMiddleware:
    """构造 middleware，model/subagent_summaries 可控（避免触发真实 LLM 初始化）"""
    return PrimaryAgentMiddleware(
        subagent_summaries=subagent_summaries or [],
        model=model or MagicMock(),
        precondition_met_codes=precondition_met_codes,
    )


# ── 职责 2: 意图捕获 ────────────────────────────────────────


async def test_no_scene_writes_pending_intent():
    """NO_SCENE 时把用户原始意图写入 AIMessage.additional_kwargs['pending_intent']"""
    messages = [HumanMessage("添加祝融号地面车")]
    request = _make_model_request(messages)
    mw = _make_middleware()

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
    mw = _make_middleware()

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert ai_msg.additional_kwargs["pending_intent"] == "添加祝融号"
    assert ai_msg.additional_kwargs["pending_subagent"] == "entity-agent"


async def test_no_scene_skips_when_intent_is_empty():
    """用户输入为空内容时不写入 pending_intent"""
    messages = [HumanMessage("")]
    request = _make_model_request(messages)
    mw = _make_middleware()

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    assert "pending_intent" not in (ai_msg.additional_kwargs or {})


async def test_no_scene_preserves_existing_pending_intent_across_turns():
    """已有 pending_intent 时，新一轮 NO_SCENE 不覆盖原始意图

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
    mw = _make_middleware()

    handler = _handler_returning(_make_agent_response_model_response("NO_SCENE"))
    response = await mw.awrap_model_call(request, handler)

    ai_msg = _find_agent_response_ai_message(response)
    # 沿用历史 pending_intent，不被「创建测试场景」覆盖
    assert ai_msg.additional_kwargs["pending_intent"] == "添加祝融号"
    assert ai_msg.additional_kwargs["pending_subagent"] == "entity-agent"


# ── 职责 3: 自动续接 ────────────────────────────────────────


async def test_auto_continue_uses_captured_subagent():
    """captured_subagent 非空时直接用，不调 LLM"""
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
    # model 是 MagicMock，如果被调用会出错；本测试断言它不被调用
    mw = _make_middleware(
        subagent_summaries=[{"name": "scene-agent", "description": "..."}],
    )

    handler = _handler_returning(_make_agent_response_model_response("SCENE_CREATED"))
    response = await mw.awrap_model_call(request, handler)

    task_tc = _find_task_tool_call(response)
    assert task_tc["args"]["description"] == "添加祝融号地面车"
    assert task_tc["args"]["subagent_type"] == "entity-agent"

    # 清除历史 messages 中的 pending_intent/pending_subagent
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.additional_kwargs:
            assert "pending_intent" not in msg.additional_kwargs
            assert "pending_subagent" not in msg.additional_kwargs


async def test_auto_continue_invokes_llm_when_no_captured_subagent():
    """无 captured_subagent 时调 LLM 分类，结果用作 subagent_type"""
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

    # mock model.with_structured_output().ainvoke()
    classifier_mock = MagicMock()
    classifier_mock.ainvoke = AsyncMock(return_value=MagicMock(subagent_type="entity-agent"))
    model_mock = MagicMock()
    model_mock.with_structured_output = MagicMock(return_value=classifier_mock)

    mw = _make_middleware(
        subagent_summaries=[
            {"name": "scene-agent", "description": "场景管理"},
            {"name": "entity-agent", "description": "实体管理"},
        ],
        model=model_mock,
    )

    handler = _handler_returning(_make_agent_response_model_response("SCENE_CREATED"))
    response = await mw.awrap_model_call(request, handler)

    task_tc = _find_task_tool_call(response)
    assert task_tc["args"]["subagent_type"] == "entity-agent"
    model_mock.with_structured_output.assert_called_once()
    classifier_mock.ainvoke.assert_awaited_once()


async def test_auto_continue_llm_returns_invalid_falls_back():
    """LLM 返回不在 summaries.name 集合时，fallback 到 summaries[0]"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            additional_kwargs={"pending_intent": "添加祝融号地面车"},
        ),
        HumanMessage("好的"),
    ]
    request = _make_model_request(messages)

    classifier_mock = MagicMock()
    classifier_mock.ainvoke = AsyncMock(return_value=MagicMock(subagent_type="non-existent-agent"))
    model_mock = MagicMock()
    model_mock.with_structured_output = MagicMock(return_value=classifier_mock)

    mw = _make_middleware(
        subagent_summaries=[
            {"name": "scene-agent", "description": "场景管理"},
            {"name": "entity-agent", "description": "实体管理"},
        ],
        model=model_mock,
    )

    handler = _handler_returning(_make_agent_response_model_response("SCENE_CREATED"))
    response = await mw.awrap_model_call(request, handler)

    task_tc = _find_task_tool_call(response)
    assert task_tc["args"]["subagent_type"] == "scene-agent"  # summaries[0]


async def test_auto_continue_skips_when_no_pending_intent():
    """messages 中无 pending_intent 时，SCENE_CREATED 不触发自动续接"""
    messages = [HumanMessage("添加祝融号")]
    request = _make_model_request(messages)
    mw = _make_middleware()

    original_response = _make_agent_response_model_response("SCENE_CREATED")
    handler = _handler_returning(original_response)
    response = await mw.awrap_model_call(request, handler)

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
    mw = _make_middleware(precondition_met_codes=frozenset())

    original_response = _make_agent_response_model_response("SCENE_CREATED")
    handler = _handler_returning(original_response)
    response = await mw.awrap_model_call(request, handler)

    assert response is original_response
    assert messages[1].additional_kwargs["pending_intent"] == "添加祝融号地面车"


# ── _extract_original_intent 静态方法 ─────────────────────────


def test_extract_original_intent_returns_latest_human():
    """直接返回最新的非空 HumanMessage content"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(content=""),
        HumanMessage("添加文昌地面站"),
    ]
    assert PrimaryAgentMiddleware._extract_last_human_intent(messages) == "添加文昌地面站"


def test_extract_original_intent_skips_confirmation_phrase():
    """最新 HumanMessage 是确认短句（如「好的」）时，回退到之前的实质意图"""
    messages = [
        HumanMessage("添加祝融号地面车"),
        AIMessage(content=""),
        HumanMessage("好的"),
    ]
    assert PrimaryAgentMiddleware._extract_last_human_intent(messages) == "添加祝融号地面车"


def test_extract_original_intent_returns_none_if_only_confirmations():
    """所有 HumanMessage 都是确认短句时返回 None"""
    messages = [
        HumanMessage("好的"),
        HumanMessage("ok"),
    ]
    assert PrimaryAgentMiddleware._extract_last_human_intent(messages) is None


def test_extract_original_intent_returns_none_if_no_human():
    """messages 中无 HumanMessage 时返回 None"""
    messages = [AIMessage(content="")]
    assert PrimaryAgentMiddleware._extract_last_human_intent(messages) is None


def test_extract_original_intent_skips_empty_content():
    """最新的 HumanMessage 内容为空时，继续向前找非空内容"""
    messages = [
        HumanMessage("添加祝融号"),
        HumanMessage(""),
    ]
    assert PrimaryAgentMiddleware._extract_last_human_intent(messages) == "添加祝融号"


# ── _extract_last_task_subagent 静态方法 ──────────────────────


def test_extract_last_task_subagent_returns_subagent_type():
    """messages 含 task tool_call 时返回 subagent_type"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "task",
                "args": {"description": "添加祝融号", "subagent_type": "entity-agent"},
                "id": "call_1",
                "type": "tool_call",
            }],
        ),
    ]
    assert PrimaryAgentMiddleware._extract_last_task_subagent(messages) == "entity-agent"


def test_extract_last_task_subagent_returns_none_if_no_task():
    """messages 无 task tool_call 时返回 None"""
    messages = [
        HumanMessage("添加祝融号"),
        AIMessage(content=""),
    ]
    assert PrimaryAgentMiddleware._extract_last_task_subagent(messages) is None
