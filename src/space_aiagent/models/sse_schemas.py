"""SSE 事件类型 + 帧序列化

定义 SSE 事件流的事件类型常量与标准帧格式。所有 SSE handler / bridge emit
站点统一引用本模块的事件类型，避免字符串字面量散落。

事件协议：
- tool_start:  工具调用入口
- tool_args:   工具参数
- tool_result: 工具执行结果
- tool_end:    工具调用结束
- interrupt:   等待用户输入或审批
- done:        对话轮次正常结束（终态）
- error:       异常（终态）

plan_snapshot / step_update / run_update 携带持久化 Run 关联字段。

终态事件（done / error）发送后 SSE 流关闭、session 注销。
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SSEEventType(StrEnum):
    """SSE 事件类型

    StreamBridge 与 WorkflowEngine 统一引用此枚举。
    """

    TOOL_START = "tool_start"
    """工具调用入口：Agent 开始执行某个工具，前端可据此展示 loading 态"""

    TOOL_ARGS = "tool_args"
    """工具参数：携带工具调用所需的参数，支持流式分段传输"""

    TOOL_RESULT = "tool_result"
    """工具执行结果：工具执行完成后的返回值 / 数据"""

    TOOL_END = "tool_end"
    """工具调用结束：一次工具调用的完整生命周期结束，前端可关闭 loading 并展示结果"""

    INTERRUPT = "interrupt"
    """中断等待：Agent 需要用户输入或审批，Run 进入 WAITING_USER 状态"""

    PLAN_SNAPSHOT = "plan_snapshot"
    """计划快照：规划阶段完成后，推送完整的执行计划概览"""

    STEP_UPDATE = "step_update"
    """步骤更新：单个执行步骤的状态变更（如 PENDING → RUNNING → SUCCEEDED）"""

    RUN_UPDATE = "run_update"
    """Run 更新："""

    DONE = "done"
    """对话轮次正常结束（终态）：发送后 SSE 流关闭、session 注销"""

    ERROR = "error"
    """异常（终态）：执行过程中发生错误，发送后 SSE 流关闭、session 注销"""


# 终态事件集合：发送后 SSE 流关闭、session 注销
TERMINAL_EVENTS: frozenset[str] = frozenset({SSEEventType.DONE.value, SSEEventType.ERROR.value})


class ChatRequest(BaseModel):
    """POST /api/v2/space/chat。"""

    content: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    message_id: str = ""
    current_scene_name: str | None = None
    scene_id: str | None = None
    scene_revision: int = Field(default=0, ge=0)
    # continue 例如：Agent 向用户发起提问/确认后，用户回复时。此时 Run 处于暂停等待状态(WAITING_USER)，用户输入注入后继续执行。
    # replace 例如 对当前 Agent 的回答不满意，想要"打断"正在进行的对话，用新的输入重新开始一轮。相当于 中止旧 Run + 创建新 Run
    mode: Literal["continue", "replace"] = "continue"


class ToolResultRequest(BaseModel):
    """带完整工作流关联字段的工具回告。"""

    thread_id: str
    run_id: str
    step_id: str
    execution_id: str
    tool_call_id: str
    idempotency_key: str
    tool_func: str
    args: dict[str, Any]
    success: bool = True
    code: str = ""
    message: str = ""
    data: dict[str, Any] | list[Any] | None = None
    scene_id: str | None = None
    scene_name: str | None = None
    scene_revision: int = Field(ge=0)


class ResumeRequest(BaseModel):
    user_input: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
