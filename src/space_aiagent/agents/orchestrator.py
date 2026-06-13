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
from langchain.agents.structured_output import ToolStrategy
from langgraph.types import Checkpointer

from space_aiagent.agents.subagents import build_model
from space_aiagent.infrastructure.config import PROJECT_ROOT
from space_aiagent.middleware import LoggingMiddleware
from space_aiagent.models.response_schema import AgentResponse
from space_aiagent.skills import SkillLoader, SkillRegistry

# 提示词路径（打包在包内）
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
# 知识文件路径（外部化到 config/，生产环境可动态修改）
_KNOWLEDGE_DIR = PROJECT_ROOT / "config" / "knowledge"


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
    checkpointer: Checkpointer,
    thread_id: str = "",
) -> "CompiledStateGraph":  # noqa: F821
    """
    创建主控 Agent

    Args:
        subagents: 子 Agent 配置字典列表
        skill_loader: Skill 加载器，用于生成摘要
        checkpointer: LangGraph Checkpointer 实例（持久化会话状态）
        thread_id: 当前会话线程 ID，用于日志追踪
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
        checkpointer=checkpointer,
        response_format=ToolStrategy(AgentResponse),
        middleware=[LoggingMiddleware(thread_id=thread_id)],
    )
    return agent
