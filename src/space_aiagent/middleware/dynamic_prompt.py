"""Agent 动态提示词中间件

每次 LLM 调用前把运行时上下文（当前场景、未来可扩展用户身份等）作为动态内容
追加到 system message，让 orchestrator 和子 Agent 的 LLM 都能感知前端状态。
"""

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
    return request.system_message
