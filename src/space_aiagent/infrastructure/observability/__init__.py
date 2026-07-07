"""
可观测性模块（OTel + Langfuse v3）

设计原则：可观测性对业务零依赖。enabled=false 时全链路 NoOp，零开销、零依赖。
"""

from space_aiagent.infrastructure.observability.tracing import (
    get_tracer,
    optional_span,
    set_span_io,
    setup_telemetry,
    shutdown_telemetry,
)

__all__ = [
    "get_tracer",
    "optional_span",
    "set_span_io",
    "setup_telemetry",
    "shutdown_telemetry",
]
