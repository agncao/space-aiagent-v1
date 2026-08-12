"""LLM 客户端构建"""

from langchain_openai import ChatOpenAI

from space_aiagent.infrastructure.config import get_settings


def build_model() -> ChatOpenAI:
    """构建 LLM 实例（OpenAI 兼容接口，支持 DeepSeek / Qwen）"""
    settings = get_settings()
    llm = settings.llm
    return ChatOpenAI(
        model=llm.model,
        openai_api_key=llm.api_key,
        openai_api_base=llm.base_url,
        temperature=llm.temperature,
        streaming=llm.streaming,
        extra_body={"enable_thinking": llm.enable_thinking},
        # 单步骤内禁用并行工具调用，避免多个 Cesium 副作用竞争同一场景版本和执行账本。
        # 经 model_kwargs 注入：_default_params 内置 **model_kwargs，而 bind_tools
        # 仅在显式传 parallel_tool_calls 时才覆盖，故此处设置能穿透 deepagents/langchain
        # 的 bind_tools 链路到达请求体。DashScope Qwen 默认即 false 并完整支持该参数。
        model_kwargs={"parallel_tool_calls": False},
    )


def build_flash_model() -> ChatOpenAI:
    """构建 LLM 实例（OpenAI 兼容接口，支持 DeepSeek / Qwen）"""
    settings = get_settings()
    llm_flash = settings.llm_flash
    return ChatOpenAI(
        model=llm_flash.model,
        openai_api_key=llm_flash.api_key,
        openai_api_base=llm_flash.base_url,
        temperature=llm_flash.temperature,
        streaming=llm_flash.streaming,
        extra_body={"enable_thinking": llm_flash.enable_thinking},
    )
