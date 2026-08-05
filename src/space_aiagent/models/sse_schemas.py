"""SSE 事件类型 + 帧序列化

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
from pydantic import BaseModel, Field
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

class ChatRequest(BaseModel):
    """POST /chat 请求体

    与 WS 时代的 UserInputMessage 等价但剥离 WS 专用字段（type），是纯 HTTP 入参。
    """

    content: str = Field(description="用户输入的文本")
    thread_id: str = Field(description="会话 thread_id（用于 checkpointer 持久化）")
    message_id: str = Field(default="", description="消息唯一ID（前端生成）")
    current_scene_name: str | None = Field(
        default=None,
        description="当前已打开的场景名（注入 SpaceAgentState 初值）",
    )


class ToolResultRequest(BaseModel):
    """POST /tool-result 请求体

    与 WS 时代的 ToolResultMessage 等价但剥离 WS 专用字段（type），
    并显式携带 thread_id（原 ToolResultMessage 通过 WSMessage 基类携带 thread_id，
    HTTP 入参需独立字段）。字段名/类型/默认值与 ToolResultMessage 对齐。
    """

    tool_func: str = Field(description="工具函数名")
    args: dict = Field(default_factory=dict, description="工具参数")
    tool_call_id: str = Field(description="工具调用ID（与 tool_start 帧一致）")
    thread_id: str = Field(description="会话 thread_id（用于定位 StreamBridge）")
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(default="", description="结果消息")
    data: dict | list | None = Field(default=None, description="返回数据")
    code: str = Field(default="", description="消息码")


class ResumeRequest(BaseModel):
    """POST /chat/{thread_id}/resume 请求体（interrupt 续跑）

    前端收到 ``event: interrupt`` 帧并收集用户决策后，通过此端点恢复 Agent。
    ``resume`` 作为 ``Command(resume=...)`` 的值送达 ``interrupt()`` 暂停点。
    格式取决于中断类型：HITL 审批（hitl_approval）用 ``decisions``，故用宽松 dict。

    HITL 审批的 resume 形如::

        {"decisions": [{"type": "approve"} | {"type": "reject"}]}
    """

    resume: dict = Field(default_factory=dict, description="恢复数据（格式取决于中断类型）")

