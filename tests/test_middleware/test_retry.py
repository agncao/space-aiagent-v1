"""RetryMiddleware 单测（Task 3 先测降级出口 shortcut 存在）"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
from pydantic import BaseModel, ValidationError

from space_aiagent.infrastructure.config import RetryConfig, RetryLLMConfig
from space_aiagent.middleware.retry import RetryMiddleware
from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode


def test_llm_unavailable_shortcut_exists():
    """SHORTCUT_RESPONSES 含 llm_unavailable，code=LLM_UNAVAILABLE，render 非空"""
    shortcut = response_constants.SHORTCUT_RESPONSES["llm_unavailable"]
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
        llm=RetryLLMConfig(
            max_attempts=3, base_delay=0.001, max_delay=0.001, retry_on_parse_error=True
        ),
    )
    mw = RetryMiddleware(cfg)
    handler = AsyncMock(side_effect=[_make_validation_error(), _make_validation_error(), "ok"])
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result == "ok"
    assert handler.call_count == 3
