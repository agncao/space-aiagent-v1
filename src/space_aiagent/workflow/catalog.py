"""领域 ActionCatalog；通用调度器只消费该声明。"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from space_aiagent.infrastructure.config import CONFIG_DIR


class ActionDefinition(BaseModel):
    '''
    {project_root}/config/actions.yaml 映射的实体类
    '''
    name: str
    description: str
    executor: str
    allowed_tools: list[str] = Field(default_factory=list)
    completion_tools: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    side_effect: bool = False
    completion_codes: list[str] = Field(default_factory=list)
    retry_policy: Literal["none", "read_safe", "side_effect_safe"] = "none"
    sandbox_policy: Literal["none", "isolated"] = "none"

    @model_validator(mode="after")
    def validate_completion_tools(self) -> "ActionDefinition":
        unknown = set(self.completion_tools) - set(self.allowed_tools)
        if unknown:
            raise ValueError(f"completion_tools 不在 allowed_tools 中: {sorted(unknown)}")
        return self


class ActionCatalog:
    '''
    全部 {project_root}/config/actions.yaml 映射的实体类ActionDefinition 的集合
    '''
    def __init__(self, actions: list[ActionDefinition]) -> None:
        self._actions = {action.name: action for action in actions}
        if len(self._actions) != len(actions):
            raise ValueError("ActionCatalog 中存在重复 action")

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "ActionCatalog":
        catalog_path = path or CONFIG_DIR / "actions.yaml"
        payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
        raw_actions = payload.get("actions", {})
        if not isinstance(raw_actions, dict) or not raw_actions:
            raise ValueError(f"ActionCatalog 为空: {catalog_path}")
        return cls([ActionDefinition(name=name, **body) for name, body in raw_actions.items()])

    def get(self, name: str) -> ActionDefinition:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise ValueError(f"未知 action: {name}") from exc

    def contains(self, name: str) -> bool:
        return name in self._actions

    def definitions(self) -> list[ActionDefinition]:
        return list(self._actions.values())

    def planner_context(self) -> str:
        lines = []
        for action in self._actions.values():
            if action.executor == "system":
                continue
            lines.append(f"- {action.name}: {action.description}")
        return "\n".join(lines)
