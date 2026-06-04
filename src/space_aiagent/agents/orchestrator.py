"""
主控 Agent（Orchestrator）

职责:
1. 接收用户输入，理解用户意图
2. 根据意图规划需要调用的子 Agent
3. 通过 DeepAgent 的 subagent 能力委派任务
4. 汇总子 Agent 结果，返回给用户

实现步骤:
1. 使用 deepagents.create_deep_agent 创建主 Agent
2. system prompt 中描述可用 Skill 摘要列表
3. 配置 LLM（DeepSeek 或 Qwen，通过 ChatOpenAI）
4. Agent 不直接绑定工具，而是通过 subagent 委派

注意:
- Orchestrator 不直接操作场景，所有操作通过子 Agent 完成
- 子 Agent 的选择基于用户意图，由 LLM 判断
"""
from space_aiagent.infrastructure.config import get_settings


def create_orchestrator():
    """
    创建主控 Agent

    步骤:
    1. 获取配置（LLM provider, model 等）
    2. 根据 provider 构建 model 字符串
       - deepseek → "openai:deepseek-chat"（配合 base_url）
       - dashscope → "openai:qwen-plus"（配合 base_url）
    3. 加载 Skill 摘要列表，构建 system prompt
    4. 使用 deepagents.create_deep_agent 创建 Agent
       from deepagents import create_deep_agent
       agent = create_deep_agent(
           model=model_string,
           tools=[],  # 主控不直接绑定工具
           system_prompt=system_prompt,
       )
    5. 配置 subagent（scene_agent, entity_agent）

    TODO: 实现
    """
    # settings = get_settings()
    # 1. 构建 model 配置
    # 2. 构建 system prompt
    # 3. 创建 deep agent
    # 4. 注册 subagents
    pass


# system prompt 模板
ORCHESTRATOR_PROMPT = """你是航天分析平台的智能助手。

你的职责是理解用户的意图，并将任务委派给合适的专业 Agent。

可用的专业能力（Skill）:
{skill_summaries}

工作流程:
1. 理解用户意图
2. 判断需要哪些 Skill
3. 委派给对应的专业 Agent
4. 汇总结果返回给用户

重要规则:
- 创建实体（卫星、地面站等）前，必须确保场景已创建
- 如果用户要求创建实体但场景不存在，先提醒用户创建场景
- 用中文回复用户
"""
