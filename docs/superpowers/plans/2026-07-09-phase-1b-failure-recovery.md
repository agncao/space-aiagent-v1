# Phase 1B 失败恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 LLM 调用与远程工具调用增加「白名单重试 + 降级」能力，临时失败自动重试，耗尽/不可恢复失败给用户结构化降级响应而非会话中断。

**Architecture:** 新建统一 `RetryMiddleware`（挂 orchestrator + 子 Agent 链最内层），内部用 tenacity 做退避引擎。LLM 可重试异常（429/超时/5xx/连接）退避重试，耗尽或不可重试时复用 `task_loop_guard` 改写 `ModelResponse` 模式降级为 `LLM_UNAVAILABLE`。工具仅重试 `asyncio.TimeoutError`（bridge 超时改抛异常），耗尽转 `ToolMessage` 给 LLM 消化；其他异常原样冒泡。复用 1A-1 的 `optional_span` 埋点，`observability.enabled=false` 时零开销。

**Tech Stack:** Python 3.13、deepagents/langchain middleware、tenacity（AsyncRetrying）、opentelemetry、pydantic、pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-07-09-phase-1b-failure-recovery-design.md`

---

## File Structure

**Create:**
- `src/space_aiagent/middleware/retry.py` — `RetryMiddleware`（LLM/工具重试 + 降级）
- `tests/test_middleware/test_retry.py` — RetryMiddleware 单测
- `tests/test_infrastructure/test_config_retry.py` — RetryConfig 加载测试
- `tests/test_bridge/__init__.py` + `tests/test_bridge/test_ws_bridge.py` — bridge 超时测试

**Modify:**
- `pyproject.toml` — 加 tenacity 依赖
- `src/space_aiagent/infrastructure/config.py` — 加 `RetryConfig`/`RetryLLMConfig`/`RetryToolConfig` + `Settings.retry` + yaml 映射
- `config/application.yaml` — 加 `retry` 段
- `config/response_templates.yaml` — 加 `LLM_UNAVAILABLE` 模板
- `src/space_aiagent/models/response_schema/agent_struct_response.py` — 加 `ResponseCode.LLM_UNAVAILABLE`
- `src/space_aiagent/models/response_schema/response_constants.py` — 加 `SHORTCUT_RESPONSES["llm_unavailable"]`
- `src/space_aiagent/bridge/ws_bridge.py` — `send_tool_call` 超时改抛 `asyncio.TimeoutError`
- `src/space_aiagent/middleware/__init__.py` — 导出 `RetryMiddleware`
- `src/space_aiagent/agents/orchestrator.py` — middleware 链加 `RetryMiddleware`
- `src/space_aiagent/agents/subagents.py` — 子 Agent middleware 链加 `RetryMiddleware`

---

## Task 1: 引入 tenacity 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 加 tenacity 到 dependencies**

在 `pyproject.toml` 的 `dependencies` 列表末尾（`"click>=8.4.1"` 之后）加：

```toml
    # 失败恢复（重试退避引擎）
    "tenacity>=8.2.3",
```

- [ ] **Step 2: 安装依赖**

Run: `pip install -e ".[dev]"`
Expected: 成功安装 tenacity

- [ ] **Step 3: 验证 import**

Run: `python -c "import tenacity; print(tenacity.__version__)"`
Expected: 打印版本号（≥8.2.3）

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: 添加 tenacity 依赖用于 Phase 1B 重试退避

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: RetryConfig 配置项

**Files:**
- Modify: `src/space_aiagent/infrastructure/config.py`
- Modify: `config/application.yaml`
- Create: `tests/test_infrastructure/test_config_retry.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_infrastructure/test_config_retry.py`:

```python
"""RetryConfig 加载测试"""

from space_aiagent.infrastructure.config import RetryConfig, RetryLLMConfig, RetryToolConfig


def test_retry_config_defaults():
    """默认值：enabled=true，llm/tool max_attempts=3，retry_on_parse_error=false"""
    cfg = RetryConfig()
    assert cfg.enabled is True
    assert cfg.llm.max_attempts == 3
    assert cfg.llm.base_delay == 1.0
    assert cfg.llm.max_delay == 10.0
    assert cfg.llm.retry_on_parse_error is False
    assert cfg.tool.max_attempts == 3
    assert cfg.tool.base_delay == 1.0
    assert cfg.tool.max_delay == 10.0


def test_retry_config_retry_on_parse_error_can_be_enabled():
    cfg = RetryConfig(llm=RetryLLMConfig(retry_on_parse_error=True))
    assert cfg.llm.retry_on_parse_error is True


def test_retry_config_loaded_from_yaml():
    """application.yaml 的 retry 段能被 _apply_yaml_to_settings 正确读取"""
    from space_aiagent.infrastructure.config import _apply_yaml_to_settings

    yaml_config = {
        "retry": {
            "enabled": False,
            "llm": {"max_attempts": 5, "base_delay": 2.0, "max_delay": 30.0, "retry_on_parse_error": True},
            "tool": {"max_attempts": 2, "base_delay": 0.5, "max_delay": 5.0},
        }
    }
    settings = _apply_yaml_to_settings(yaml_config)
    assert settings.retry.enabled is False
    assert settings.retry.llm.max_attempts == 5
    assert settings.retry.llm.retry_on_parse_error is True
    assert settings.retry.tool.max_attempts == 2
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_infrastructure/test_config_retry.py -v`
Expected: FAIL — `ImportError: cannot import name 'RetryConfig'`

- [ ] **Step 3: 实现 RetryConfig 配置类**

在 `src/space_aiagent/infrastructure/config.py` 的 `ObservabilityConfig` 类之后、`Settings` 类之前插入：

```python
class RetryLLMConfig(BaseSettings):
    """LLM 调用重试配置"""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0
    # ToolStrategy 结构化输出解析失败(ValidationError)是否重试，默认 false
    retry_on_parse_error: bool = False


class RetryToolConfig(BaseSettings):
    """工具调用重试配置"""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0


class RetryConfig(BaseSettings):
    """失败恢复配置（Phase 1B）

    enabled=false 时 RetryMiddleware 透传，零开销。
    与 observability.enabled 独立（retry 是业务恢复，observability 是观测）。
    """

    enabled: bool = True
    llm: RetryLLMConfig = Field(default_factory=RetryLLMConfig)
    tool: RetryToolConfig = Field(default_factory=RetryToolConfig)
```

在 `Settings` 类的 `observability` 字段之后加：

```python
    retry: RetryConfig = Field(default_factory=RetryConfig)
```

- [ ] **Step 4: 在 `_apply_yaml_to_settings` 加 retry 映射**

在 `_apply_yaml_to_settings` 函数的 `flat["observability"] = ...` 块之后、`return Settings(**flat)` 之前插入：

```python
    retry_cfg = yaml_config.get("retry", {})
    llm_retry_cfg = retry_cfg.get("llm", {})
    tool_retry_cfg = retry_cfg.get("tool", {})
    flat["retry"] = RetryConfig(
        enabled=retry_cfg.get("enabled", True),
        llm=RetryLLMConfig(
            max_attempts=llm_retry_cfg.get("max_attempts", 3),
            base_delay=float(llm_retry_cfg.get("base_delay", 1.0)),
            max_delay=float(llm_retry_cfg.get("max_delay", 10.0)),
            retry_on_parse_error=llm_retry_cfg.get("retry_on_parse_error", False),
        ),
        tool=RetryToolConfig(
            max_attempts=tool_retry_cfg.get("max_attempts", 3),
            base_delay=float(tool_retry_cfg.get("base_delay", 1.0)),
            max_delay=float(tool_retry_cfg.get("max_delay", 10.0)),
        ),
    )
```

- [ ] **Step 5: 在 application.yaml 加 retry 段**

在 `config/application.yaml` 的 `observability:` 段之后追加：

```yaml
retry:
  # 失败恢复（Phase 1B）：false 时 RetryMiddleware 透传，零开销
  # 与 observability.enabled 独立
  enabled: true
  llm:
    max_attempts: 3
    base_delay: 1.0
    max_delay: 10.0
    # ToolStrategy 结构化输出解析失败是否重试，默认 false
    retry_on_parse_error: false
  tool:
    max_attempts: 3
    base_delay: 1.0
    max_delay: 10.0
```

- [ ] **Step 6: 跑测试验证通过**

Run: `pytest tests/test_infrastructure/test_config_retry.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add src/space_aiagent/infrastructure/config.py config/application.yaml tests/test_infrastructure/test_config_retry.py
git commit -m "feat: 添加 RetryConfig 配置（Phase 1B 失败恢复）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: LLM_UNAVAILABLE 降级出口

**Files:**
- Modify: `src/space_aiagent/models/response_schema/agent_struct_response.py`
- Modify: `config/response_templates.yaml`
- Modify: `src/space_aiagent/models/response_schema/response_constants.py`
- Create: `tests/test_middleware/test_retry.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_middleware/test_retry.py`:

```python
"""RetryMiddleware 单测（Task 3 先测降级出口 shortcut 存在）"""

from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode


def test_llm_unavailable_shortcut_exists():
    """SHORTCUT_RESPONSES 含 llm_unavailable，code=LLM_UNAVAILABLE，render 非空"""
    shortcut = response_constants.SHORTCUT_RESPONSES["llm_unavailable"]
    assert shortcut.code == ResponseCode.LLM_UNAVAILABLE
    assert shortcut.status == "error"
    text = response_util.render(shortcut)
    assert len(text) > 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_middleware/test_retry.py::test_llm_unavailable_shortcut_exists -v`
Expected: FAIL — `KeyError: 'llm_unavailable'`（或 `AttributeError: LLM_UNAVAILABLE`）

- [ ] **Step 3: 加 ResponseCode.LLM_UNAVAILABLE**

在 `src/space_aiagent/models/response_schema/agent_struct_response.py` 的 `OUT_OF_SCOPE = auto()` 之后加：

```python
    # 系统失败（LLM 调用重试耗尽 / 不可重试失败，由 RetryMiddleware 注入）
    LLM_UNAVAILABLE = auto()
```

- [ ] **Step 4: 加 LLM_UNAVAILABLE 模板**

在 `config/response_templates.yaml` 末尾（`OUT_OF_SCOPE:` 块之后）追加：

```yaml

LLM_UNAVAILABLE:
  template: |-
    AI 服务暂时不可用，请稍后重试。

    如果多次出现此提示，请联系管理员检查 AI 服务状态。
```

- [ ] **Step 5: 加 SHORTCUT_RESPONSES["llm_unavailable"]**

在 `src/space_aiagent/models/response_schema/response_constants.py` 的 `SHORTCUT_RESPONSES` dict 内、`"task_loop_guard"` 条目之后加：

```python
    "llm_unavailable": AgentResponse(
        status="error",
        code=ResponseCode.LLM_UNAVAILABLE,
        summary=DEFAULT_TEMPLATES["LLM_UNAVAILABLE"],
        suggestions=[],
    ),
```

- [ ] **Step 6: 跑测试验证通过**

Run: `pytest tests/test_middleware/test_retry.py::test_llm_unavailable_shortcut_exists -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/space_aiagent/models/response_schema/agent_struct_response.py config/response_templates.yaml src/space_aiagent/models/response_schema/response_constants.py tests/test_middleware/test_retry.py
git commit -m "feat: 添加 LLM_UNAVAILABLE 降级出口（ResponseCode + 模板 + shortcut）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: bridge 超时改抛 asyncio.TimeoutError

**Files:**
- Modify: `src/space_aiagent/bridge/ws_bridge.py`
- Create: `tests/test_bridge/__init__.py`
- Create: `tests/test_bridge/test_ws_bridge.py`

- [ ] **Step 1: 建 test_bridge 包**

Create `tests/test_bridge/__init__.py` (空文件):

```python
```

- [ ] **Step 2: 写失败测试**

Create `tests/test_bridge/test_ws_bridge.py`:

```python
"""bridge send_tool_call 超时行为测试

Phase 1B：超时改抛 asyncio.TimeoutError（不再返回 {success:false} dict），
由 RetryMiddleware 捕获重试。
"""

import asyncio

import pytest
from unittest.mock import AsyncMock

from space_aiagent.bridge.ws_bridge import WSBridge


async def test_send_tool_call_timeout_raises_timeout_error():
    """超时 → 抛 asyncio.TimeoutError，pending 已清理"""
    ws = AsyncMock()
    bridge = WSBridge(ws, "thread-1")

    with pytest.raises(asyncio.TimeoutError):
        await bridge.send_tool_call("createScenario", {}, timeout=0.01)

    # 超时后 pending 必须清理，避免 future 泄漏
    assert len(bridge._pending) == 0


async def test_send_tool_call_success_returns_result():
    """正常返回 → 返回前端结果 dict"""
    ws = AsyncMock()
    bridge = WSBridge(ws, "thread-1")

    # 在另一个协程里 resolve future
    async def _resolve():
        # 轮询等 pending future 出现（send_tool_call 内才注册，避免时序竞争）
        while not bridge._pending:
            await asyncio.sleep(0.001)
        for fid, fut in list(bridge._pending.items()):
            fut.set_result({"success": True, "data": "ok"})

    asyncio.create_task(_resolve())
    result = await bridge.send_tool_call("createScenario", {}, timeout=1.0)
    assert result["success"] is True
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/test_bridge/test_ws_bridge.py -v`
Expected: `test_send_tool_call_timeout_raises_timeout_error` FAIL — 断言 `raises(asyncio.TimeoutError)` 失败（现在返回 dict 不抛异常）；`test_send_tool_call_success_returns_result` 应 PASS

- [ ] **Step 4: 改 send_tool_call 超时分支**

在 `src/space_aiagent/bridge/ws_bridge.py` 把 `send_tool_call` 的 79-88 行（`try: result = await asyncio.wait_for(...)` 块）替换为：

```python
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(
                "收到 tool_result", tool_call_id=tool_call_id, thread_id=self._thread_id, success=result.get("success")
            )
            return result
        except TimeoutError:
            # Phase 1B：超时改抛 asyncio.TimeoutError（不再吞成 {success:false} dict），
            # 由 RetryMiddleware.awrap_tool_call 捕获并退避重试
            self._pending.pop(tool_call_id, None)
            logger.warning("tool_call 超时", tool_func=tool_func, tool_call_id=tool_call_id, thread_id=self._thread_id)
            raise
```

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest tests/test_bridge/test_ws_bridge.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/space_aiagent/bridge/ws_bridge.py tests/test_bridge/
git commit -m "feat: bridge 超时改抛 asyncio.TimeoutError 供 RetryMiddleware 重试

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: RetryMiddleware — LLM 重试与降级

**Files:**
- Create: `src/space_aiagent/middleware/retry.py`
- Modify: `tests/test_middleware/test_retry.py`

- [ ] **Step 1: 写失败测试（追加到 test_retry.py）**

在 `tests/test_middleware/test_retry.py` 顶部 import 区追加，并新增测试函数：

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from pydantic import BaseModel, ValidationError

from space_aiagent.infrastructure.config import RetryConfig, RetryLLMConfig
from space_aiagent.middleware.retry import RetryMiddleware


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
        llm=RetryLLMConfig(max_attempts=3, base_delay=0.001, max_delay=0.001, retry_on_parse_error=True),
    )
    mw = RetryMiddleware(cfg)
    handler = AsyncMock(side_effect=[_make_validation_error(), _make_validation_error(), "ok"])
    result = await mw.awrap_model_call(SimpleNamespace(), handler)
    assert result == "ok"
    assert handler.call_count == 3
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_middleware/test_retry.py -v`
Expected: 新增的 6 个 LLM 测试 FAIL — `ImportError: cannot import name 'RetryMiddleware'`（`test_llm_unavailable_shortcut_exists` 应仍 PASS）

- [ ] **Step 3: 实现 RetryMiddleware（LLM 部分）**

Create `src/space_aiagent/middleware/retry.py`:

```python
"""失败恢复中间件（Phase 1B）

挂在 orchestrator 和子 Agent 链最内层（紧贴真实 LLM/工具调用）。
- LLM: 可重试异常(429/超时/5xx/连接)退避重试，耗尽/不可重试降级 LLM_UNAVAILABLE
- 工具: 仅 asyncio.TimeoutError 退避重试，耗尽转 ToolMessage 给 LLM；
  其他异常原样冒泡（连接中断不处理，当前无重连机制）

可观测性：复用 optional_span 子 span + before_sleep 回调写 retry.* attribute。
observability.enabled=false 时 span 是 NoOp，set_attribute 无副作用，retry 仍正常工作。
"""

from collections.abc import Awaitable, Callable
from typing import Any

import openai
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from opentelemetry.trace.span import Span
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from space_aiagent.infrastructure.config import RetryConfig
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span
from space_aiagent.infrastructure.utils import message_util
from space_aiagent.models.response_schema import response_constants, response_util

logger = get_logger(__name__)

# LLM 可重试异常基础集合（retry_on_parse_error=true 时追加 ValidationError）
_BASE_RETRYABLE_LLM_ERRORS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def _build_retryable_llm_errors(retry_on_parse_error: bool) -> tuple[type[Exception], ...]:
    if retry_on_parse_error:
        return _BASE_RETRYABLE_LLM_ERRORS + (ValidationError,)
    return _BASE_RETRYABLE_LLM_ERRORS


def _make_before_sleep(span: Span) -> Callable[[Any], None]:
    """构造 before_sleep 回调：写 retry.* 到 span + warning 日志"""

    def _log(retry_state: Any) -> None:
        attempt = getattr(retry_state, "attempt_number", 0)
        span.set_attribute("retry.attempt", attempt)
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is not None:
            span.set_attribute("retry.last_error", type(exc).__name__)
        logger.warning("重试", attempt=attempt, error=type(exc).__name__ if exc else None)

    return _log


class RetryMiddleware(AgentMiddleware):
    """LLM/工具调用重试 + 降级"""

    def __init__(self, config: RetryConfig) -> None:
        self._config = config
        self._retryable_llm_errors = _build_retryable_llm_errors(config.llm.retry_on_parse_error)

    def _degrade_llm(self) -> ModelResponse:
        """复用 task_loop_guard 改写模式：构造 LLM_UNAVAILABLE 降级 ModelResponse"""
        shortcut = response_constants.SHORTCUT_RESPONSES["llm_unavailable"]
        display = response_util.render(shortcut)
        return message_util.build_primary_agent_response(display, shortcut, "call_llm_unavailable")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not self._config.enabled:
            return await handler(request)

        with optional_span("llm.retry") as span:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(self._config.llm.max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._config.llm.base_delay, max=self._config.llm.max_delay
                ),
                retry=retry_if_exception_type(self._retryable_llm_errors),
                before_sleep=_make_before_sleep(span),
            )
            try:
                return await retrying(handler, request)
            except RetryError:
                span.set_attribute("retry.outcome", "exhausted")
                logger.warning("LLM 重试耗尽，降级 LLM_UNAVAILABLE")
                return self._degrade_llm()
            except (openai.APIError, ValidationError) as e:
                # 不可重试异常：tenacity 不重试直接抛（不包装成 RetryError）
                span.set_attribute("retry.outcome", "non_retryable")
                span.set_attribute("retry.error", type(e).__name__)
                logger.warning("LLM 不可重试异常，降级 LLM_UNAVAILABLE", error=type(e).__name__)
                return self._degrade_llm()
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_middleware/test_retry.py -v`
Expected: 7 passed（含 Task 3 的 shortcut 测试）

- [ ] **Step 5: Commit**

```bash
git add src/space_aiagent/middleware/retry.py tests/test_middleware/test_retry.py
git commit -m "feat: RetryMiddleware LLM 重试与降级（Phase 1B）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: RetryMiddleware — 工具重试

**Files:**
- Modify: `src/space_aiagent/middleware/retry.py`
- Modify: `tests/test_middleware/test_retry.py`

- [ ] **Step 1: 写失败测试（追加到 test_retry.py）**

```python
import json

from langchain_core.messages import ToolMessage


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
    handler = AsyncMock(side_effect=[asyncio.TimeoutError(), {"success": True}])
    result = await mw.awrap_tool_call(_make_tool_request(), handler)
    assert result == {"success": True}
    assert handler.call_count == 2


async def test_tool_timeout_exhausted_returns_tool_message():
    """TimeoutError 重试耗尽 → 转 ToolMessage(success=false, 超时) 给 LLM"""
    mw = RetryMiddleware(_fast_cfg())
    handler = AsyncMock(side_effect=asyncio.TimeoutError())
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_middleware/test_retry.py -v -k "tool"`
Expected: 5 个工具测试 FAIL — `AttributeError: 'RetryMiddleware' object has no attribute 'awrap_tool_call'`

- [ ] **Step 3: 实现 awrap_tool_call**

在 `src/space_aiagent/middleware/retry.py` 顶部 import 区加（Task 5 阶段未用、本任务才用到的）：

```python
import asyncio
import json

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
```

在 `RetryMiddleware` 类的 `awrap_model_call` 方法之后加：

```python
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if not self._config.enabled:
            return await handler(request)

        with optional_span("tool.retry") as span:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(self._config.tool.max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._config.tool.base_delay, max=self._config.tool.max_delay
                ),
                retry=retry_if_exception_type(asyncio.TimeoutError),
                before_sleep=_make_before_sleep(span),
            )
            try:
                return await retrying(handler, request)
            except RetryError:
                # TimeoutError 重试耗尽 → 转 ToolMessage 给 LLM 消化（不短路，不新增 code）
                span.set_attribute("retry.outcome", "exhausted")
                tool_call_id = request.tool_call.get("id", "")
                logger.warning("工具超时重试耗尽，转 ToolMessage", tool_call_id=tool_call_id)
                return ToolMessage(
                    content=json.dumps(
                        {"success": False, "message": "工具调用超时，请稍后重试"},
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                )
            # 非 TimeoutError 异常 tenacity 不重试、原样 reraise，不在此 catch，冒泡到 websocket handler
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_middleware/test_retry.py -v`
Expected: 全部 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/space_aiagent/middleware/retry.py tests/test_middleware/test_retry.py
git commit -m "feat: RetryMiddleware 工具重试（仅 TimeoutError，耗尽转 ToolMessage）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: 挂载到 orchestrator + 子 Agent

**Files:**
- Modify: `src/space_aiagent/middleware/__init__.py`
- Modify: `src/space_aiagent/agents/orchestrator.py`
- Modify: `src/space_aiagent/agents/subagents.py`

- [ ] **Step 1: 导出 RetryMiddleware**

在 `src/space_aiagent/middleware/__init__.py` 加 import 与 `__all__` 条目：

```python
from space_aiagent.middleware.retry import RetryMiddleware
```

`__all__` 列表内加 `"RetryMiddleware",`（按字母序放在 `"ResponseStabilizationMiddleware"` 之后）。

- [ ] **Step 2: 挂到 orchestrator 链最内层**

在 `src/space_aiagent/agents/orchestrator.py` 的 import 区，把：

```python
from space_aiagent.middleware import (
    PrimaryAgentMiddleware,
    agents_dynamic_prompt,
)
```

改为：

```python
from space_aiagent.middleware import (
    PrimaryAgentMiddleware,
    RetryMiddleware,
    agents_dynamic_prompt,
)
```

在 `create_orchestrator` 的 `middleware=[...]` 列表末尾（`agents_dynamic_prompt,` 之后）加 `RetryMiddleware`（最内层）：

```python
        middleware=[
            PrimaryAgentMiddleware(
                thread_id=thread_id,
                task_loop_threshold=settings.agent.primary_task_threshold,
            ),
            agents_dynamic_prompt,
            RetryMiddleware(settings.retry),
        ],
```

- [ ] **Step 3: 挂到子 Agent 链最内层**

在 `src/space_aiagent/agents/subagents.py` 的 import 区，把：

```python
from space_aiagent.middleware import (
    SubagentToolValidationMiddleware,
    agents_dynamic_prompt,
)
```

改为：

```python
from space_aiagent.middleware import (
    RetryMiddleware,
    SubagentToolValidationMiddleware,
    agents_dynamic_prompt,
)
```

并在文件顶部 import 区加（若尚无）：

```python
from space_aiagent.infrastructure.config import get_settings
```

在 `load_subagents` 的 `subagents.append({...})` 的 `"middleware"` 列表末尾加 `RetryMiddleware`：

```python
                "middleware": [
                    SubagentToolValidationMiddleware(
                        tool_groups=agent_cfg["tools"],
                        agent_name=agent_cfg["name"],
                    ),
                    agents_dynamic_prompt,
                    RetryMiddleware(get_settings().retry),
                ],
```

- [ ] **Step 4: 验证现有测试不破**

Run: `pytest -q`
Expected: 全部既有测试 PASS（本任务只接线，不改逻辑）

- [ ] **Step 5: 验证 middleware 顺序假设**

Run: `python -c "from space_aiagent.agents.orchestrator import create_orchestrator; print('import ok')"`
Expected: 打印 `import ok`（无导入错误）

> 若 deepagents middleware 列表顺序语义与设计假设相反（第一个=最内层而非最外层），需把 `RetryMiddleware` 移到列表首位。验证方法：在 `RetryMiddleware.awrap_model_call` 内打日志观察它是否在 `PrimaryAgentMiddleware` 之前/之后进入。当前设计假设「列表首个=最外层」，故 RetryMiddleware 放末尾=最内层。

- [ ] **Step 6: Commit**

```bash
git add src/space_aiagent/middleware/__init__.py src/space_aiagent/agents/orchestrator.py src/space_aiagent/agents/subagents.py
git commit -m "feat: RetryMiddleware 挂载到 orchestrator 与子 Agent 链最内层

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 完成验证

- [ ] **全量测试**：`pytest -q` 全绿
- [ ] **Lint**：`ruff check src/ tests/` 无错
- [ ] **手动 smoke**：启动 `python -m space_aiagent.main`，发一个 user_input，确认正常流程不受影响；模拟 LLM 限流（如临时改错 LLM_API_KEY 触发 401→BadRequestError）确认降级为「AI 服务暂时不可用」而非崩溃
