"""SSE 事件类型 + 帧序列化（SSE 迁移 T2）

定义 SSE 事件流的事件类型常量与标准帧格式。所有 SSE handler / bridge emit
站点统一引用本模块的事件类型，避免字符串字面量散落。

事件协议（共 8 类，详见 docs/superpowers/specs/2026-07-21-sse-migration-design.md §4.3）：
- token:       LLM token 流式（on_chat_model_stream）
- tool_start:  工具调用入口
- tool_args:   工具参数
- tool_result: 工具执行结果
- tool_end:    工具调用结束
- interrupt:   中断（协议就位，暂不触发）
- done:        对话轮次正常结束（终态）
- error:       异常（终态）

终态事件（done / error）发送后 SSE 流关闭、session 注销。
"""

import json
from enum import StrEnum


class SSEEventType(StrEnum):
    """SSE 事件类型

    成员值与 StreamBridge 已有的字符串字面量（tool_start/tool_args/tool_result/
    tool_end）精确对齐，token/interrupt/done/error 供后续 T3/T7 使用。
    """

    TOKEN = "token"
    TOOL_START = "tool_start"
    TOOL_ARGS = "tool_args"
    TOOL_RESULT = "tool_result"
    TOOL_END = "tool_end"
    INTERRUPT = "interrupt"
    DONE = "done"
    ERROR = "error"


# 终态事件集合：发送后 SSE 流关闭、session 注销
TERMINAL_EVENTS: frozenset[str] = frozenset(
    {SSEEventType.DONE.value, SSEEventType.ERROR.value}
)


def format_sse_frame(event: str, data: dict) -> str:
    """生成标准 SSE 帧

    输出格式（SSE spec）：
        event: <event>\\n
        data: <json>\\n
        \\n

    - 两个字段行（event: / data:），各自以 ``\\n`` 结尾
    - 末尾空行（``\\n``）作为帧分隔符
    - json.dumps 单行输出（无内部换行），故只需一行 ``data:``
    - ensure_ascii=False：保留中文可读性（仓库面向用户的文本为中文）

    Args:
        event: 事件类型（建议传 SSEEventType 成员或其字符串值）
        data: 事件数据，将以 JSON 序列化进 ``data:`` 行

    Returns:
        标准 SSE 帧字符串
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
