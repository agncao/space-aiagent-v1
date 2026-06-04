"""
Skill 动态加载器

根据 Agent 当前任务的需要，动态加载指定 Skill 的工具函数。

核心流程:
1. Agent 判断需要哪个 Skill
2. 调用 SkillLoader.load_skill("scene_management")
3. 加载器动态导入 tools.py 模块
4. 提取所有 @tool 装饰的函数
5. 返回工具列表给 Agent 绑定
"""
import importlib
import importlib.util
import logging
from pathlib import Path

from .registry import SkillInfo, SkillRegistry

logger = logging.getLogger(__name__)


class SkillLoader:
    """
    Skill 动态加载器

    TODO: 实现以下功能
    1. 根据技能名从注册表获取 SkillInfo
    2. 动态导入 tools.py 模块
    3. 提取工具函数
    4. 支持通过 bridge 包装工具（远程执行模式）
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry
        self._loaded: dict[str, list] = {}  # skill_name -> tools

    def load_skill(self, skill_name: str) -> list:
        """
        加载指定 Skill 的所有工具

        步骤:
        1. 从注册表获取 SkillInfo
        2. 检查是否已加载（缓存）
        3. 动态导入 tools.py
        4. 提取 @tool 装饰的函数
        5. 缓存并返回

        Returns:
            工具函数列表
        """
        # TODO: 实现
        return []

    def load_skills(self, skill_names: list[str]) -> list:
        """
        批量加载多个 Skill 的工具

        Args:
            skill_names: Skill 名称列表

        Returns:
            合并后的工具函数列表
        """
        tools = []
        for name in skill_names:
            tools.extend(self.load_skill(name))
        return tools

    def unload_skill(self, skill_name: str) -> None:
        """
        卸载 Skill（从缓存移除）

        TODO: 实现
        """
        self._loaded.pop(skill_name, None)

    def _import_tools_module(self, skill_dir: Path, skill_name: str):
        """
        动态导入 tools.py 模块

        步骤:
        1. 构建 tools.py 的完整路径
        2. 使用 importlib.util.spec_from_file_location 创建模块规格
        3. 创建并执行模块
        4. 返回模块对象
        """
        # TODO: 实现
        pass

    def _extract_tools(self, module) -> list:
        """
        从模块中提取所有工具函数

        步骤:
        1. 遍历模块的所有属性
        2. 筛选被 @tool 或 @StructuredTool 装饰的函数
        3. 返回工具列表

        提示: 可以检查函数是否有 .name 属性（LangChain tool 的特征）
        """
        # TODO: 实现
        return []
