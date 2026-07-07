"""
可观测性 tracing 初始化

职责：
1. enabled=false 时立即返回，全局 TracerProvider 默认是 ProxyTracer（NoOp），业务零开销
2. enabled=true 时构建 TracerProvider（Resource + Sampler），由 Langfuse SDK 自动挂载 LangfuseSpanProcessor

接入 OTel 标准 API：
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("operation") as span:
        ...
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, sampling
from opentelemetry.trace import Tracer
from opentelemetry.trace.span import Span

from space_aiagent.infrastructure.config import ObservabilityConfig, Settings
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.utils.string_util import truncate

logger = get_logger(__name__)

_initialized = False


def setup_telemetry(settings: Settings) -> None:
    """初始化全局 TracerProvider。

    enabled=false 时直接返回，OTel SDK 默认是 NoOp（业务零开销）。
    """
    global _initialized
    if _initialized:
        logger.warning("observability.already_initialized")
        return

    cfg: ObservabilityConfig = settings.observability
    if not cfg.enabled:
        logger.info("observability.disabled")
        return

    # base_url 是 Langfuse 实例根地址（不含 /api/public/otel 后缀，SDK 内部拼接）
    base_url = cfg.langfuse_endpoint.rsplit("/api/public/otel", 1)[0]

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": cfg.service_name,
                "service.version": cfg.service_version,
            }
        ),
        sampler=sampling.TraceIdRatioBased(cfg.sampler_ratio),
    )
    trace.set_tracer_provider(provider)

    # Langfuse v3 SDK 自动将 LangfuseSpanProcessor 挂到 global TracerProvider
    # 延迟 import 避免 enabled=false 时引入 langfuse 依赖
    from langfuse import Langfuse

    Langfuse(
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
        base_url=base_url,
        sample_rate=cfg.sampler_ratio,
    )

    _initialized = True
    logger.info(
        "observability.ready",
        service_name=cfg.service_name,
        langfuse_endpoint=base_url,
        sampler_ratio=cfg.sampler_ratio,
    )


def shutdown_telemetry() -> None:
    """进程退出时 flush SpanProcessor，避免 trace 丢失。

    幂等：多次调用安全。
    """
    global _initialized
    if not _initialized:
        return

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    _initialized = False
    logger.info("observability.shutdown")


def get_tracer(name: str) -> Tracer:
    """获取 tracer。enabled=false 时返回 NoOp tracer，调用方零开销。"""
    return trace.get_tracer(name)


@contextmanager
def optional_span(name: str, **attributes: Any) -> Iterator[Span]:
    """便利 context manager：自动从 enabled 状态决定是否创建 span。

    用法：
        with optional_span("orchestrator.llm", thread_id=tid) as span:
            result = handler(request)
            span.set_attribute("response.code", code)
    """
    tracer = get_tracer("space_aiagent")
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, v)
        yield span

def set_span_io(
    span: Span,
    *,
    input: Any = None,
    output: Any = None,
    max_len: int = 4000,
) -> None:
    """统一设置 Langfuse 识别的 span input/output 属性。

    Langfuse 通过 input.value / output.value（OpenInference 标准）识别 observation IO；
    root span 的 IO 会自动成为 trace 级 IO（root observation 规则），所以同一套属性
    既覆盖 trace 列表预览（root span），也覆盖 observation 详情（子 span）。

    - str → text/plain 原样写入
    - 其他对象 → json.dumps(ensure_ascii=False, default=str) → application/json
    - 序列化异常 → str() 兜底 → text/plain
    - 最后整体截断（复用 string_util.truncate，带"[截断, 总长N]"提示）

    enabled=false 时 span 是 NoOp（ProxySpan），set_attribute 无副作用，零开销。
    """
    if input is not None:
        _set_io_value(span, "input", input, max_len)
    if output is not None:
        _set_io_value(span, "output", output, max_len)


def _set_io_value(span: Span, kind: str, value: Any, max_len: int) -> None:
    if isinstance(value, str):
        raw = value
        mime_type = "text/plain"
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
            mime_type = "application/json"
        except (TypeError, ValueError):
            raw = str(value)
            mime_type = "text/plain"
    span.set_attribute(f"{kind}.value", truncate(raw, max_len=max_len))
    span.set_attribute(f"{kind}.mime_type", mime_type)
