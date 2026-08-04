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
from deepagents.backends.composite import CompositeBackend
from langchain.agents.structured_output import ToolStrategy
from langgraph.types import Checkpointer

from space_aiagent.agents.state import SpaceAgentState
from space_aiagent.infrastructure.config import PROJECT_ROOT, get_settings
from space_aiagent.infrastructure.llm import build_model
from space_aiagent.middleware import (
    PrimaryAgentMiddleware,
    RetryMiddleware,
    agents_dynamic_prompt,
)

# 提示词路径（打包在包内）
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
# 知识文件路径（外部化到 config/，生产环境可动态修改）
_KNOWLEDGE_DIR = PROJECT_ROOT / "config" / "knowledge"
# Skill 包路径（src 内，按 scope 组织：main/scene/entity）
_SKILLS_DIR = PROJECT_ROOT / "src" / "space_aiagent" / "skills"


def _build_subagent_summaries(subagents: list[dict]) -> str:
    """构建子 Agent 摘要文本（数据来自 subagents.yaml 的 agents[].description）"""
    if not subagents:
        return "（暂无可用子 Agent）"
    return "\n".join(f"- {s['name']}: {s['description']}" for s in subagents)


def _build_system_prompt(subagents: list[dict]) -> str:
    """构建系统提示词（不含领域知识，知识通过 memory 加载）"""
    template = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")
    return template.format(
        agent_summaries=_build_subagent_summaries(subagents),
    )


def create_orchestrator(
    subagents: list[dict],
    checkpointer: Checkpointer,
    thread_id: str = "",
) -> "CompiledStateGraph":  # noqa: F821
    """
    创建主控 Agent

    Args:
        subagents: 子 Agent 配置字典列表
        checkpointer: LangGraph Checkpointer 实例（持久化会话状态）
        thread_id: 当前会话线程 ID，用于日志追踪
    """
    system_prompt = _build_system_prompt(subagents)
    settings = get_settings()
    model = build_model()

    # 复合后端：按路径前缀路由到不同根目录。
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(_KNOWLEDGE_DIR), virtual_mode=True),
        routes={"/skills/": FilesystemBackend(root_dir=str(_SKILLS_DIR), virtual_mode=True)},
    )

    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        subagents=subagents,
        backend=backend,
        memory=["AGENTS.md"],
        checkpointer=checkpointer,
        state_schema=SpaceAgentState,
        middleware=[
            PrimaryAgentMiddleware(
                thread_id=thread_id,
                task_loop_threshold=settings.agent.primary_task_threshold,
            ),
            agents_dynamic_prompt,
            RetryMiddleware(settings.retry),
        ],
    )
    return agent
