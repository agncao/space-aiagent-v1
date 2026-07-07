"""
structlog processor：注入 trace_id / span_id

把当前 OTel span 的 trace_id/span_id 写到每条 log 里，便于在 Loki/ELK 等
日志聚合系统中按 trace_id 反查 Langfuse trace 详情。

零开销保障：observability.enabled=false 时 OTel 返回 INVALID span context，
is_valid=False 提前 return，不影响日志性能。
"""

from opentelemetry import trace


def add_trace_info(_, __, event_dict):
    """structlog processor：注入 trace_id / span_id（如果当前有 active span）"""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        event_dict["trace_id"] = f"{ctx.trace_id:032x}"
        event_dict["span_id"] = f"{ctx.span_id:016x}"
    return event_dict
