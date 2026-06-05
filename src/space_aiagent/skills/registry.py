"""
Skill 注册表

扫描 skills/ 目录下的所有子目录，加载每个 Skill 的 skill.yaml，
构建 Skill 摘要列表供 Agent 查询。
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
    """Skill 注册表"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}

    def discover(self) -> None:
        """
        扫描并注册所有 Skill

        遍历 SKILLS_DIR 下的所有子目录，检查是否包含 skill.yaml，
        解析为 SkillInfo 并注册。
        """
        if not SKILLS_DIR.is_dir():
            logger.warning("skills 目录不存在: %s", SKILLS_DIR)
            return

        for child in sorted(SKILLS_DIR.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("_"):
                continue

            yaml_path = child / "skill.yaml"
            if not yaml_path.exists():
                continue

            try:
                with open(yaml_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                name = data.get("name", child.name)
                info = SkillInfo(
                    name=name,
                    description=data.get("description", ""),
                    triggers=data.get("triggers", []),
                    skill_dir=child,
                )
                self._skills[name] = info
                logger.debug("注册 Skill: %s (%s)", name, child.name)
            except Exception:
                logger.exception("加载 skill.yaml 失败: %s", yaml_path)

        logger.info("共注册 %d 个 Skill: %s", len(self._skills), list(self._skills.keys()))

    def get_skill(self, name: str) -> SkillInfo | None:
        """按名称获取 Skill"""
        return self._skills.get(name)

    def get_summaries(self) -> list[dict[str, str]]:
        """
        获取所有 Skill 的摘要

        返回格式:
        [
            {"name": "scene_management", "description": "..."},
            ...
        ]
        """
        return [{"name": info.name, "description": info.description} for info in self._skills.values()]

    def list_skill_names(self) -> list[str]:
        """列出所有已注册的 Skill 名称"""
        return list(self._skills.keys())
