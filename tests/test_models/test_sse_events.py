"""SSE 事件类型 + format_sse_frame 帧序列化测试。"""

import json

from space_aiagent.models.sse_events import (
    TERMINAL_EVENTS,
    SSEEventType,
    format_sse_frame,
)


def test_format_sse_frame_basic() -> None:
    """帧包含 event: / data: 两行 + 空行终止符；data JSON 可往返解析。"""
    frame = format_sse_frame(SSEEventType.TOKEN, {"content": "hello", "source": "scene-agent"})

    # 以 \n\n 终止（最后两个字符是空行分隔符）
    assert frame.endswith("\n\n")
    # event 行存在
    assert "event: token\n" in frame
    # data 行存在且内容可被 json.loads 往返解析回原 dict
    assert frame.index("event: token\n") < frame.index("data: ")
    data_line = next(
        line for line in frame.split("\n") if line.startswith("data: ")
    )
    payload = json.loads(data_line[len("data: "):])
    assert payload == {"content": "hello", "source": "scene-agent"}


def test_format_sse_frame_chinese_not_escaped() -> None:
    """中文不得被 \\uXXXX 转义（ensure_ascii=False），保持流式输出可读。"""
    frame = format_sse_frame(SSEEventType.DONE, {"thread_id": "t1", "content": "已创建场景「测试场景」"})

    # 反例：若误开 ensure_ascii，会包含 \uXXXX 转义
    assert "\\u" not in frame
    # 正例：原始中文字符出现在输出中
    assert "已创建场景" in frame
    assert "测试场景" in frame


def test_format_sse_frame_event_field_uses_provided_value() -> None:
    """event 字段直接使用入参（str 值），不做额外加工。"""
    frame = format_sse_frame("tool_start", {"tool_func": "createScenario", "tool_call_id": "abc"})
    assert frame.startswith("event: tool_start\n")


def test_terminal_events_membership() -> None:
    """TERMINAL_EVENTS 仅含 done / error，其余事件均非终态。"""
    assert frozenset({"done", "error"}) == TERMINAL_EVENTS
    assert SSEEventType.DONE.value in TERMINAL_EVENTS
    assert SSEEventType.ERROR.value in TERMINAL_EVENTS
    # 中间帧均非终态
    for non_terminal in (
        SSEEventType.TOKEN,
        SSEEventType.TOOL_START,
        SSEEventType.TOOL_ARGS,
        SSEEventType.TOOL_RESULT,
        SSEEventType.TOOL_END,
        SSEEventType.INTERRUPT,
    ):
        assert non_terminal.value not in TERMINAL_EVENTS


def test_sse_event_type_values_match_stream_bridge_literals() -> None:
    """事件类型字符串值与 StreamBridge 已使用的字面量精确对齐。

    StreamBridge（T1）用字符串字面量 emit tool_start/tool_args/tool_result/tool_end，
    本模块的事件类型值必须与之完全一致，后续 wiring 才能无缝替换。
    """
    assert SSEEventType.TOOL_START == "tool_start"
    assert SSEEventType.TOOL_ARGS == "tool_args"
    assert SSEEventType.TOOL_RESULT == "tool_result"
    assert SSEEventType.TOOL_END == "tool_end"
    assert SSEEventType.TOKEN == "token"
    assert SSEEventType.INTERRUPT == "interrupt"
    assert SSEEventType.DONE == "done"
    assert SSEEventType.ERROR == "error"
