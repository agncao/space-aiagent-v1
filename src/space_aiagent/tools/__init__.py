"""
工具注册表

设计:
1. 每个工具组（tool group）是一个独立目录，包含 tools.py（@tool 装饰的工具函数）
2. tools/registry.py 通过目录扫描自动发现所有工具组与工具
3. 子智能体通过 get_tools(["scene_management"]) 获取所需工具
4. Orchestrator 通过 subagents.yaml 的 agent description 做路由，不再依赖组级描述

使用方式:
    from space_aiagent.tools.registry import get_all_groups, get_tools

    tools = get_tools(["scene_management"])
    all_groups = get_all_groups()  # CLI 内省用
"""

from space_aiagent.tools.registry import get_all_groups, get_tools

__all__ = ["get_all_groups", "get_tools"]
