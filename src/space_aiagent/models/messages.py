"""
WebSocket 消息类型定义

定义所有 WebSocket 通信的消息格式，确保前后端消息结构一致
"""

from pydantic import BaseModel, Field

from .enums import WSMessageType


class WSMessage(BaseModel):
    """WebSocket 消息基类"""

    type: WSMessageType
    thread_id: str


class UserInputMessage(WSMessage):
    """用户输入消息（前端 -> 后端）"""

    type: WSMessageType = WSMessageType.USER_INPUT
    content: str = Field(description="用户输入的文本")
    message_id: str = Field(default="", description="消息唯一ID")
    current_scene_name: str | None = Field(
        default=None,
        description="当前已打开的场景名",
    )


class ToolCallMessage(WSMessage):
    """工具调用指令（后端 -> 前端）"""

    type: WSMessageType = WSMessageType.TOOL_CALL
    tool_func: str = Field(description="工具函数名")
    tool_func_args: dict = Field(default_factory=dict, description="工具参数")
    tool_call_id: str = Field(default="", description="工具调用ID")
    message_id: str = Field(default="")


class ToolResultMessage(WSMessage):
    """工具执行结果（前端 -> 后端）"""

    type: WSMessageType = WSMessageType.TOOL_RESULT
    tool_func: str = Field(description="工具函数名")
    args: dict = Field(default_factory=dict, description="工具参数")
    tool_call_id: str = Field(default="", description="工具调用ID")
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(default="", description="结果消息")
    data: dict | list | None = Field(default=None, description="返回数据")
    code: str = Field(default="", description="消息码")


class AIMessage(WSMessage):
    """AI 文本回复（后端 -> 前端）"""

    type: WSMessageType = WSMessageType.AI_MESSAGE
    content: str = Field(description="AI 回复文本")


class EndMessage(WSMessage):
    """对话轮次结束（后端 -> 前端）"""

    type: WSMessageType = WSMessageType.END


class ErrorMessage(WSMessage):
    """错误消息（后端 -> 前端）"""

    type: WSMessageType = WSMessageType.ERROR
    message: str = Field(description="错误信息")
