"""
主控 Agent（Orchestrator）

职责:
1. 接收用户输入，理解用户意图
2. 根据意图规划需要调用的子 Agent
3. 通过 DeepAgent 的 subagent 能力委派任务
4. 汇总子 Agent 结果，返回给用户
"""

from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from space_aiagent.agents.subagents import build_model
from space_aiagent.skills import SkillLoader, SkillRegistry

# 提示词和知识文件路径
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def _build_skill_summaries(registry: SkillRegistry) -> str:
    """构建 Skill 摘要文本"""
    summaries = registry.get_summaries()
    if not summaries:
        return "（暂无可用 Skill）"
    return "\n".join(f"- {s['name']}: {s['description']}" for s in summaries)


def _build_system_prompt(registry: SkillRegistry) -> str:
    """构建系统提示词（不含领域知识，知识通过 memory 加载）"""
    template = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")
    return template.format(
        skill_summaries=_build_skill_summaries(registry),
    )


def create_orchestrator(
    subagents: list[dict],
    skill_loader: SkillLoader,
) -> "CompiledStateGraph":  # noqa: F821
    """
    创建主控 Agent

    Args:
        subagents: 子 Agent 配置字典列表
        skill_loader: Skill 加载器，用于生成摘要
    """
    registry = skill_loader._registry
    system_prompt = _build_system_prompt(registry)
    model = build_model()

    # 知识文件通过 FilesystemBackend + memory 加载
    backend = FilesystemBackend(
        root_dir=str(_KNOWLEDGE_DIR),
        virtual_mode=True,
    )

    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        subagents=subagents,
        backend=backend,
        memory=["AGENTS.md"],
    )
    return agent
