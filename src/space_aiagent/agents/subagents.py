"""
子 Agent 配置加载器

从 config/subagents.yaml 读取 Agent 声明，结合静态工具注册表和提示词文件，
构建 create_deep_agent 所需的 subagent 配置列表。
"""

from pathlib import Path

import yaml

from space_aiagent.infrastructure.config import CONFIG_DIR
from space_aiagent.infrastructure.llm import build_model
from space_aiagent.middleware import ToolValidationMiddleware
from space_aiagent.tools.registry import get_tools

# 路径常量
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_SUBAGENTS_CONFIG = CONFIG_DIR / "subagents.yaml"


def load_subagents() -> list[dict]:
    """
    从 YAML 配置加载所有子 Agent

    Returns:
        subagent 配置字典列表，可直接传给 create_deep_agent 的 subagents 参数
    """
    config_text = _SUBAGENTS_CONFIG.read_text(encoding="utf-8")
    config = yaml.safe_load(config_text)

    model = build_model()
    subagents: list[dict] = []

    for agent_cfg in config["agents"]:
        tools = get_tools(agent_cfg["tools"])
        prompt = (_PROMPTS_DIR / agent_cfg["prompt_file"]).read_text(encoding="utf-8")

        subagents.append({
            "name": agent_cfg["name"],
            "description": agent_cfg["description"],
            "model": model,
            "tools": tools,
            "system_prompt": prompt,
            "middleware": [ToolValidationMiddleware()],
        })

    return subagents
