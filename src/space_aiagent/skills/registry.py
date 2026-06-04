"""
Skill 注册表

扫描 skills/ 目录下的所有子目录，加载每个 Skill 的 skill.yaml，
构建 Skill 摘要列表供 Agent 查询。

skill.yaml 格式示例:
    name: scene_management
    description: "场景管理：创建、重命名、清除、查询航天场景"
    triggers:
      - 创建场景
      - 打开场景
      - 清除场景
      - 查询场景
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent


@dataclass
class SkillInfo:
    """单个 Skill 的元信息"""
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    skill_dir: Path = field(default_factory=Path)
    _tools: list | None = field(default=None, repr=False)


class SkillRegistry:
    """
    Skill 注册表

    TODO: 实现以下功能
    1. 扫描 skills/ 目录下的所有子目录
    2. 读取每个子目录中的 skill.yaml
    3. 解析为 SkillInfo 对象并缓存
    4. 提供按名称查询的方法
    5. 提供获取所有 Skill 摘要的方法（给 Agent 的 system prompt 用）
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}

    def discover(self) -> None:
        """
        扫描并注册所有 Skill

        步骤:
        1. 遍历 SKILLS_DIR 下的所有子目录
        2. 检查是否包含 skill.yaml
        3. 解析 skill.yaml 为 SkillInfo
        4. 注册到 _skills 字典
        """
        # TODO: 实现
        pass

    def get_skill(self, name: str) -> SkillInfo | None:
        """按名称获取 Skill"""
        return self._skills.get(name)

    def get_summaries(self) -> list[dict[str, str]]:
        """
        获取所有 Skill 的摘要

        返回格式:
        [
            {"name": "scene_management", "description": "..."},
            {"name": "entity_management", "description": "..."},
            ...
        ]

        用途: 注入到 Agent 的 system prompt 中，
              让 Agent 知道有哪些 Skill 可用
        """
        return [
            {"name": info.name, "description": info.description}
            for info in self._skills.values()
        ]

    def list_skill_names(self) -> list[str]:
        """列出所有已注册的 Skill 名称"""
        return list(self._skills.keys())
