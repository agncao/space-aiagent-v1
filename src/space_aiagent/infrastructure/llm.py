"""LLM 客户端构建"""

from langchain_openai import ChatOpenAI

from space_aiagent.infrastructure.config import get_settings


def build_model() -> ChatOpenAI:
    """构建 LLM 实例（OpenAI 兼容接口，支持 DeepSeek / Qwen）"""
    llm = get_settings().llm
    return ChatOpenAI(
        model=llm.model,
        openai_api_key=llm.api_key,
        openai_api_base=llm.base_url,
        temperature=llm.temperature,
        streaming=llm.streaming,
        extra_body={"enable_thinking": False},
    )
