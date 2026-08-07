"""
子 Agent 配置加载器

从 config/subagents.yaml 读取 Agent 声明，结合静态工具注册表和提示词文件，
构建 create_deep_agent 所需的 subagent 配置列表。
"""

from pathlib import Path

from deepagents.backends.protocol import BackendProtocol

from space_aiagent.agents import subagents_util
from space_aiagent.infrastructure.backend import build_agent_backend
from space_aiagent.infrastructure.config import get_settings
from space_aiagent.infrastructure.llm import build_flash_model, build_model
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.middleware import (
    RetryMiddleware,
    SkillRoutingMiddleware,
    SubagentToolValidationMiddleware,
)
from space_aiagent.infrastructure.skill.catalog import SkillCatalog
from space_aiagent.tools.registry import get_tools

logger = get_logger(__name__)

# 提示词路径（打包在包内：src/space_aiagent/prompts/）
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_SKILL_USAGE_PROMPT = (_PROMPTS_DIR / "skill_usage.md").read_text(encoding="utf-8")


def load_subagents(backend: BackendProtocol | None = None) -> list[dict]:
    """
    从 YAML 配置加载所有子 Agent

    Returns:
        subagent 配置字典列表，可直接传给 create_deep_agent 的 subagents 参数
    """
    config = subagents_util.load_subagents_yaml_config()

    backend = backend or build_agent_backend()
    model = build_model()
    flash_model = build_flash_model()
    retry_config = get_settings().retry
    subagents: list[dict] = []

    for agent_cfg in config["agents"]:
        tools = get_tools(agent_cfg["tools"])
        prompt = (_PROMPTS_DIR / agent_cfg["prompt_file"]).read_text(encoding="utf-8")
        skills_paths = list(agent_cfg.get("skills", []))
        if skills_paths:
            prompt = f"{prompt.rstrip()}\n\n{_SKILL_USAGE_PROMPT}"
        business_tool_names = {tool.name for tool in tools}
        try:
            catalog = SkillCatalog.from_backend(backend, skills_paths, business_tool_names)
        except Exception as exc:
            logger.exception(
                "skill.load_failed",
                agent=agent_cfg["name"],
                error=type(exc).__name__,
            )
            raise

        middleware = []
        if skills_paths:
            middleware.append(
                SkillRoutingMiddleware(
                    agent_name=agent_cfg["name"],
                    catalog=catalog,
                    business_tool_names=business_tool_names,
                    router_model=flash_model,
                    retry_config=retry_config,
                )
            )
        middleware.extend(
            [
                SubagentToolValidationMiddleware(
                    tool_groups=agent_cfg["tools"],
                    agent_name=agent_cfg["name"],
                ),
                RetryMiddleware(retry_config),
            ]
        )

        subagent: dict = {
            "name": agent_cfg["name"],
            "description": agent_cfg["description"],
            "model": model,
            "tools": tools,
            "system_prompt": prompt,
            "middleware": middleware,
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
        if skills_paths:
            subagent["skills"] = skills_paths

        subagents.append(subagent)

    return subagents
