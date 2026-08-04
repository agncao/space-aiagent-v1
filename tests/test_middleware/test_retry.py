"""RetryMiddleware 单测（Task 3 先测降级出口 shortcut 存在）"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ValidationError

from space_aiagent.infrastructure.config import RetryConfig, RetryLLMConfig
from space_aiagent.middleware.retry import RetryMiddleware
from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode, AgentResponse


def test_llm_unavailable_shortcut_exists():
    """SHORTCUT_RESPONSES 含 llm_unavailable，code=LLM_UNAVAILABLE，render 非空"""
    shortcut = response_constants.SHORTCUT_RESPONSES[ResponseCode.LLM_UNAVAILABLE]
    assert shortcut.code == ResponseCode.LLM_UNAVAILABLE
    assert shortcut.status == "error"
    text = response_util.render(shortcut)
    assert len(text) > 0


# ── 辅助构造 openai 异常 ──
def _make_rate_limit_error() -> openai.RateLimitError:
    req = httpx.Request("POST", "https://api.example.com")
    return openai.RateLimitError(
        message="rate limited",
        response=httpx.Response(status_code=429, request=req),
        body=None,
    )


def _make_bad_request_error() -> openai.BadRequestError:
    req = httpx.Request("POST", "https://api.example.com")
    return openai.BadRequestError(
        message="bad request",
        response=httpx.Response(status_code=400, request=req),
        body=None,
    )


def _make_validation_error() -> ValidationError:
    class _M(BaseModel):
        x: int

    try:
        _M(x="not_int")
    except ValidationError as e:
        return e
    raise RuntimeError("unreachable")


def _fast_cfg() -> RetryConfig:
    """退避极小的配置，测试不卡顿"""
    return RetryConfig(
        enabled=True,
        llm=RetryLLMConfig(max_attempts=3, base_delay=0.001, max_delay=0.001),
    )


async def test_disabled_passthrough_llm():
    """enabled=false → 透传不重试"""
    cfg = RetryConfig(enabled=False)
    mw = RetryMiddleware(cfg)
    handler = AsyncMock(return_value="ok")
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result == "ok"
    handler.assert_called_once()


async def test_llm_retry_succeeds_after_transient_error():
    """可重试异常 → 退避重试后成功"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=[_make_rate_limit_error(), "ok"])
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result == "ok"
    assert handler.call_count == 2


async def test_llm_exhausted_degrades_to_unavailable():
    """可重试异常重试耗尽 → 降级 LLM_UNAVAILABLE"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=_make_rate_limit_error())
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result.structured_response.code == "LLM_UNAVAILABLE"
    assert handler.call_count == 3


async def test_llm_unavailable_shortcut_is_plain_text_aimessage():
    """退役：降级 ModelResponse 是纯文本 AIMessage（content=summary，无 AgentResponse tool_call）。

    其 content 由 langgraph messages 流 emit 走 token 流到前端（不再依赖 done.content
    渲染）。structured_response 保留作观测/流程控制元数据。response_format 退役后
    AgentResponse 不是注册工具，若仍构造其 tool_call 会让 ToolNode 报错，故必须纯文本。
    """
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=_make_rate_limit_error())
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    # structured_response 保留（观测/流程控制元数据）
    assert result.structured_response.code == "LLM_UNAVAILABLE"
    # AIMessage 纯文本：content == 降级 summary，无 tool_calls
    assert len(result.result) == 1
    ai = result.result[0]
    assert ai.content == result.structured_response.summary
    assert not ai.tool_calls


async def test_llm_non_retryable_degrades_immediately():
    """不可重试异常(BadRequestError) → 不重试，直接降级"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=_make_bad_request_error())
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result.structured_response.code == "LLM_UNAVAILABLE"
    assert handler.call_count == 1


async def test_llm_parse_error_not_retried_by_default():
    """retry_on_parse_error=false（默认）→ ValidationError 不重试直接降级"""
    mw = RetryMiddleware(_fast_cfg())  # 默认 retry_on_parse_error=False
    handler = AsyncMock(side_effect=_make_validation_error())
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result.structured_response.code == "LLM_UNAVAILABLE"
    assert handler.call_count == 1


async def test_llm_parse_error_retried_when_configured():
    """retry_on_parse_error=true → ValidationError 进重试白名单"""
    cfg = RetryConfig(
        enabled=True,
        llm=RetryLLMConfig(max_attempts=3, base_delay=0.001, max_delay=0.001, retry_on_parse_error=True),
    )
    mw = RetryMiddleware(cfg)
    handler = AsyncMock(side_effect=[_make_validation_error(), _make_validation_error(), "ok"])
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result == "ok"
    assert handler.call_count == 3


# ── Task 6: awrap_tool_call 工具重试 ──


def _make_tool_request(tool_call_id: str = "call_test"):
    """构造 mock ToolCallRequest（ducktyping，需 tool_call 属性）"""
    return type(
        "Req",
        (),
        {"tool_call": {"name": "add_point_entity", "args": {}, "id": tool_call_id}},
    )()


async def test_tool_timeout_retried_then_succeeds():
    """TimeoutError → 退避重试后成功"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=[TimeoutError(), {"success": True}])
    result = await mw.awrap_tool_call(_make_tool_request(), handler)
    assert result == {"success": True}
    assert handler.call_count == 2


async def test_tool_timeout_exhausted_returns_tool_message():
    """TimeoutError 重试耗尽 → 转 ToolMessage(success=false, 超时) 给 LLM"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=TimeoutError())
    result = await mw.awrap_tool_call(_make_tool_request("call_x"), handler)
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call_x"
    data = json.loads(result.content)
    assert data["success"] is False
    assert "超时" in data["message"]
    assert handler.call_count == 3


async def test_tool_non_timeout_exception_propagates():
    """非 TimeoutError 异常（如 ValueError/WebSocketDisconnect）→ 不重试，原样冒泡"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=ValueError("bug"))
    with pytest.raises(ValueError):
        await mw.awrap_tool_call(_make_tool_request(), handler)
    assert handler.call_count == 1


async def test_tool_success_false_not_retried():
    """业务失败(success=false) → 不重试，原样返回给 LLM 消化"""
    mw = RetryMiddleware(_fast_cfg())
    biz_fail = {"success": False, "message": "实体已存在"}
    handler = AsyncMock(return_value=biz_fail)
    result = await mw.awrap_tool_call(_make_tool_request(), handler)
    assert result == biz_fail
    handler.assert_called_once()


async def test_disabled_passthrough_tool():
    """enabled=false → 工具调用透传不重试"""
    cfg = RetryConfig(enabled=False)
    mw = RetryMiddleware(cfg)
    handler = AsyncMock(return_value={"success": True})
    result = await mw.awrap_tool_call(_make_tool_request(), handler)
    assert result == {"success": True}
    handler.assert_called_once()
