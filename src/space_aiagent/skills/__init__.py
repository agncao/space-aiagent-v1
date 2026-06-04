"""
Skill 渐进式披露框架

核心设计:
1. 每个 Skill 是一个独立目录，包含 skill.yaml（描述）和 tools.py（工具函数）
2. SkillRegistry 扫描并注册所有 Skill
3. SkillLoader 按需动态加载 Skill 的工具到 Agent
4. Agent 启动时只看到 Skill 摘要列表，根据任务按需加载具体工具

使用方式:
    from space_aiagent.skills import SkillRegistry, SkillLoader

    registry = SkillRegistry()
    registry.discover()                    # 扫描所有 Skill
    summaries = registry.get_summaries()   # 获取摘要给 Agent

    loader = SkillLoader(registry)
    tools = loader.load_skill("scene_management")  # 按需加载工具
"""
from .loader import SkillLoader
from .registry import SkillRegistry

__all__ = ["SkillRegistry", "SkillLoader"]
