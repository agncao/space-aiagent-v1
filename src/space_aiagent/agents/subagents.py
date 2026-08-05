"""
子 Agent 配置加载器

从 config/subagents.yaml 读取 Agent 声明，结合静态工具注册表和提示词文件，
构建 create_deep_agent 所需的 subagent 配置列表。
"""

from pathlib import Path

from space_aiagent.agents import subagents_util
from space_aiagent.infrastructure.config import get_settings
from space_aiagent.infrastructure.llm import build_model
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.middleware import (
    RetryMiddleware,
    SceneAgentHitlMiddleware,
    SubagentToolValidationMiddleware,
    agents_dynamic_prompt,
)
from space_aiagent.tools.registry import get_tools

logger = get_logger(__name__)

# 提示词路径（打包在包内：src/space_aiagent/prompts/）
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

def load_subagents() -> list[dict]:
    """
    从 YAML 配置加载所有子 Agent

    Returns:
        subagent 配置字典列表，可直接传给 create_deep_agent 的 subagents 参数
    """
    config = subagents_util.load_subagents_yaml_config()

    model = build_model()
    subagents: list[dict] = []

    for agent_cfg in config["agents"]:
        tools = get_tools(agent_cfg["tools"])
        prompt = (_PROMPTS_DIR / agent_cfg["prompt_file"]).read_text(encoding="utf-8")

        subagent:dict = {
                "name": agent_cfg["name"],
                "description": agent_cfg["description"],
                "model": model,
                "tools": tools,
                "system_prompt": prompt,
                "middleware": [
                    SubagentToolValidationMiddleware(
                        tool_groups=agent_cfg["tools"],
                        agent_name=agent_cfg["name"],
                    ),
                    agents_dynamic_prompt,
                    RetryMiddleware(get_settings().retry),
                ],
            }

        # scene-agent 专属：open_scenario 的两个条件性 HITL 中断点（中间件驱动）
        # 排在 SubagentToolValidationMiddleware 之后（内层）：后者在无场景时返回
        # Command(goto=END) 且不调 handler，故本中间件的 awrap_tool_call 不会触发，
        # 避免无场景时误入 HITL。其余子 Agent 不挂载。
        # if agent_cfg["name"] == "scene-agent":
        #     subagent["middleware"].insert(1, SceneAgentHitlMiddleware())

        # 可选配置
        if interrupt_on := agent_cfg.get("interrupt_on"):
            subagent["interrupt_on"] = interrupt_on

        # skills 是 backend 虚拟路径（如 /skills/scene/），由 orchestrator 的 CompositeBackend
        # 路由解析到 src/space_aiagent/skills/<scope>/。原样透传给 deepagents（list[str]），
        # 不做文件系统拼接——SkillsMiddleware 经 backend.ls/download_files 读取。
        if skills_paths := agent_cfg.get("skills"):
            subagent["skills"] = list(skills_paths)

        subagents.append(subagent)



    return subagents
