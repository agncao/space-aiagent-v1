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
        # 禁用并行工具调用：scene 工具（query_scenario/open_scenario/create_scenario
        # 等）成功路径返回 Command(update={"scenario_query_results"|"current_scene_name": ...})，
        # 这两个 state 字段是 last_value 通道（state.py 无 reducer）。LLM 一次响应里
        # 并行发起两个工具调用时，同一 step 对 last_value 通道写入两次会抛
        # InvalidUpdateError: Can receive only one value per step。
        # 关闭并行调用从根上消除该冲突（共享场景状态本也不该被并发改写）。
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