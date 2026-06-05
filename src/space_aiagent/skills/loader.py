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

import importlib.util
import logging
from pathlib import Path

from langchain_core.tools import BaseTool

from .registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillLoader:
    """Skill 动态加载器"""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry
        self._loaded: dict[str, list[BaseTool]] = {}

    def load_skill(self, skill_name: str) -> list[BaseTool]:
        """
        加载指定 Skill 的所有工具

        步骤:
        1. 从注册表获取 SkillInfo
        2. 检查是否已加载（缓存）
        3. 动态导入 tools.py
        4. 提取 @tool 装饰的函数
        5. 缓存并返回
        """
        if skill_name in self._loaded:
            return self._loaded[skill_name]

        info = self._registry.get_skill(skill_name)
        if info is None:
            logger.warning("Skill 未注册: %s", skill_name)
            return []

        module = self._import_tools_module(info.skill_dir, skill_name)
        if module is None:
            return []

        tools = self._extract_tools(module)
        self._loaded[skill_name] = tools
        logger.info("加载 Skill [%s]: %d 个工具", skill_name, len(tools))
        return tools

    def load_skills(self, skill_names: list[str]) -> list[BaseTool]:
        """
        批量加载多个 Skill 的工具
        """
        tools: list[BaseTool] = []
        for name in skill_names:
            tools.extend(self.load_skill(name))
        return tools

    def unload_skill(self, skill_name: str) -> None:
        """卸载 Skill（从缓存移除）"""
        self._loaded.pop(skill_name, None)

    def _import_tools_module(self, skill_dir: Path, skill_name: str):
        """
        动态导入 tools.py 模块
        """
        tools_path = skill_dir / "tools.py"
        if not tools_path.exists():
            logger.warning("Skill [%s] 没有 tools.py: %s", skill_name, tools_path)
            return None

        module_name = f"space_aiagent.skills.{skill_dir.name}.tools"
        spec = importlib.util.spec_from_file_location(module_name, str(tools_path))
        if spec is None or spec.loader is None:
            logger.warning("无法创建模块规格: %s", tools_path)
            return None

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            return module
        except Exception:
            logger.exception("导入 tools.py 失败: %s", tools_path)
            return None

    def _extract_tools(self, module) -> list[BaseTool]:
        """
        从模块中提取所有工具函数

        遍历模块的所有属性，筛选被 @tool 装饰的函数（BaseTool 实例）。
        """
        tools: list[BaseTool] = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, BaseTool):
                tools.append(attr)
        return tools
