"""Agent 动态提示词中间件

每次 LLM 调用前把运行时上下文（当前场景、未来可扩展用户身份等）作为动态内容
追加到 system message，让 orchestrator 和子 Agent 的 LLM 都能感知前端状态。

current_scene_name 通过 SpaceAgentState 跨节点同步：websocket handler 把
user_input.current_scene_name 注入 astream_events input，工具返回 Command
更新 state。本中间件只读不写。
"""
from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import dynamic_prompt
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage


@dynamic_prompt
def agents_dynamic_prompt(request: ModelRequest) -> SystemMessage:
    """每次 LLM 调用前把动态上下文段追加到 system message

    用 deepagents 内置 append_to_system_message 处理 content blocks 模式
    （SystemMessage.content 可能是 str 也可能是 list[ContentBlock]，由
    MemoryMiddleware / SubagentsMiddleware / TodoListMiddleware 决定）。
    与 deepagents 其他内置 middleware 的拼接风格保持一致。
    """
    scene_name = (
        request.state.get("current_scene_name")
        if isinstance(request.state, dict)
        else getattr(request.state, "current_scene_name", None)
    )
    hint = f"当前场景:{scene_name}, 如果不为 None或者空字符串，说明当前场景已打开"
    return append_to_system_message(request.system_message, hint)
