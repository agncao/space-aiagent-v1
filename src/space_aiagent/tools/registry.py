"""
工具注册表 — 基于目录扫描的自动发现机制

设计约定:
- tools/ 下每个子目录是一个工具组（group）
- 组内任意 .py 文件中通过 @tool 装饰的 BaseTool 实例会被自动收集
- 新增工具: 只需在 tools/<group>/ 下写 @tool 函数，无需改本文件
- 新增工具组: 在 tools/ 下建子目录 + 写工具代码 + 在 subagents.yaml 挂到某 agent

组描述不在此处维护，由 subagents.yaml 的 agents[].description 提供
（Agent 描述是组能力的超集，且 orchestrator 实际委派对象是 Agent）
"""

import importlib
import re
from contextvars import ContextVar
from pathlib import Path

from langchain_core.tools import BaseTool

from space_aiagent.infrastructure.logging import get_logger

logger = get_logger(__name__)

_TOOLS_ROOT = Path(__file__).parent


def _discover_groups() -> dict[str, list[BaseTool]]:
    """
    扫描 tools/ 目录，发现所有工具组及其工具

    Returns:
        {组名: [BaseTool, ...]}（组内按 tool.name 去重，按文件名字母序）
    """
    groups: dict[str, list[BaseTool]] = {}

    # 组目录按字母序遍历，保证启动日志稳定
    for entry in sorted(_TOOLS_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue

        group_name = entry.name
        group_module = f"space_aiagent.tools.{group_name}"

        # 扫描组内所有 .py 模块（按文件名排序，保证工具顺序稳定）
        tools: list[BaseTool] = []
        for sub_entry in sorted(entry.iterdir()):
            if not sub_entry.is_file():
                continue
            if sub_entry.name.startswith("_") or not sub_entry.name.endswith(".py"):
                continue

            module_name = sub_entry.stem
            full_module = f"{group_module}.{module_name}"
            try:
                mod = importlib.import_module(full_module)
            except Exception as e:
                logger.warning("跳过工具模块，导入失败", module=full_module, error=str(e))
                continue

            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if isinstance(obj, BaseTool):
                    tools.append(obj)

        # 按 tool.name 去重
        seen: set[str] = set()
        deduped: list[BaseTool] = []
        for t in tools:
            if t.name in seen:
                continue
            deduped.append(t)
            seen.add(t.name)

        groups[group_name] = deduped
        logger.debug(
            "发现工具组",
            group=group_name,
            tool_count=len(deduped),
            tools=[t.name for t in deduped],
        )

    return groups


# 模块加载时一次性扫描，后续调用零开销
_GROUPS = _discover_groups()


def get_tools(groups: list[str]) -> list[BaseTool]:
    """
    获取指定工具组的所有工具（按 tool.name 去重）

    Args:
        groups: 工具组名列表，如 ["entity_management", "orbit_management"]

    Returns:
        去重后的工具列表，可直接传给子智能体的 tools 字段
    """
    seen: set[str] = set()
    tools: list[BaseTool] = []
    for group in groups:
        for tool in _GROUPS.get(group, []):
            if tool.name in seen:
                continue
            tools.append(tool)
            seen.add(tool.name)
    return tools


def get_all_groups() -> dict[str, list[BaseTool]]:
    """
    返回所有工具组及其工具（供 CLI 内省使用）

    Returns:
        {组名: [BaseTool, ...]} 的浅拷贝
    """
    return dict(_GROUPS)


def _extract_first_sentence(description: str) -> str:
    """提取 description 首段的首句作为用户面能力描述

    用于 suggestions 候选集生成：每个工具的 description 首句作为该工具对应的
    "用户面能力描述"，用于反向校验 LLM 生成的 suggestions 是否越界。

    示例:
      "创建航天场景。场景是所有实体的容器..." → "创建航天场景"
      "查询场景信息。不传参数时查询当前场景。\\n\\n参数:..." → "查询场景信息"
      "重命名/修改当前场景。\\n\\n参数:..." → "重命名/修改当前场景"

    前提：工具 description 第一段第一句必须是用户视角的能力描述（不是开发者注释）。
    """
    first_paragraph = description.split("\n\n")[0].strip()
    parts = re.split(r"[。\.！？\?]", first_paragraph, maxsplit=1)
    first_sentence = parts[0].strip() if parts else ""
    return " ".join(first_sentence.split())  # 归一化空白


def get_suggestion_candidates(group_names: list[str]) -> list[str]:
    """按工具组生成 suggestion 候选集（从 description 首句提取）

    Args:
        group_names: 工具组名列表，如 ["scene_management"]

    Returns:
        去重保序的能力描述列表
    """
    candidates: list[str] = []
    for group_name in group_names:
        for tool in _GROUPS.get(group_name, []):
            first_sentence = _extract_first_sentence(tool.description)
            if first_sentence:
                candidates.append(first_sentence)
    return list(dict.fromkeys(candidates))  # 去重保序


# ContextVar：保存当前 agent 的 suggestion 候选集
# 接入点：ToolValidationMiddleware.awrap_model_call（已挂在每个子 agent 上，
# 通过 subagents.py 传入 tool_groups 参数），在每个 LLM 调用前 set
# 参考现有 bridge_var 模式
current_suggestion_candidates_var: ContextVar[frozenset[str]] = ContextVar(
    "current_suggestion_candidates", default=frozenset()
)
