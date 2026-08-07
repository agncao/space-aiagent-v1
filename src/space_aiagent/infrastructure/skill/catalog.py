"""Skill 目录加载与产品级质量校验。"""

import re
from dataclasses import dataclass

import yaml
from deepagents.backends.protocol import BackendProtocol

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillCatalogError(ValueError):
    """Skill 包不符合仓库协议或无法读取。"""


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """构建期缓存的单个 Skill。"""

    name: str
    description: str
    path: str
    content: str
    allowed_tools: frozenset[str]
    enforcement: str | None

    @property
    def is_required(self) -> bool:
        return self.enforcement == "required"


class SkillCatalog:
    """按 Agent scope 加载 Skill，并校验其工具契约。"""

    def __init__(self, skills: list[SkillDefinition]) -> None:
        self._skills = tuple(skills)
        self._by_name = {skill.name: skill for skill in skills}

    @property
    def skills(self) -> tuple[SkillDefinition, ...]:
        return self._skills

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)

    @property
    def governed_tools(self) -> frozenset[str]:
        return frozenset(tool_name for skill in self._skills if skill.is_required for tool_name in skill.allowed_tools)

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise SkillCatalogError(f"未知 Skill：{name}") from exc

    def select(self, names: list[str]) -> list[SkillDefinition]:
        """按目录发现顺序返回选中 Skill，并拒绝未知或重复名称。"""
        if len(names) != len(set(names)):
            raise SkillCatalogError("路由结果包含重复 Skill")
        unknown = set(names) - self.names
        if unknown:
            raise SkillCatalogError(f"路由结果包含未知 Skill：{', '.join(sorted(unknown))}")
        selected = set(names)
        return [skill for skill in self._skills if skill.name in selected]

    @classmethod
    def from_backend(
        cls,
        backend: BackendProtocol,
        sources: list[str],
        available_tools: set[str],
    ) -> "SkillCatalog":
        """从 Backend 构建目录；当前约定 Skill 在构建期读取、重启后生效。"""
        definitions: list[SkillDefinition] = []
        seen_names: set[str] = set()
        for source in sources:
            listing = backend.ls(source)
            if listing.error is not None:
                raise SkillCatalogError(f"无法列出 Skill 目录 {source}：{listing.error}")
            paths = [f"{str(entry['path']).rstrip('/')}/SKILL.md" for entry in listing.entries if entry.get("is_dir")]
            for response in backend.download_files(paths):
                if response.error is not None or response.content is None:
                    raise SkillCatalogError(f"无法读取 Skill {response.path}：{response.error or 'empty_content'}")
                try:
                    content = response.content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SkillCatalogError(f"Skill 必须使用 UTF-8：{response.path}") from exc
                definition = _parse_skill(content, response.path, available_tools)
                if definition.name in seen_names:
                    raise SkillCatalogError(f"Skill 名称重复：{definition.name}")
                seen_names.add(definition.name)
                definitions.append(definition)
        return cls(definitions)


def _parse_skill(content: str, path: str, available_tools: set[str]) -> SkillDefinition:
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise SkillCatalogError(f"Skill 缺少有效 YAML frontmatter：{path}")
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillCatalogError(f"Skill frontmatter 无法解析：{path}") from exc
    if not isinstance(raw, dict):
        raise SkillCatalogError(f"Skill frontmatter 必须是对象：{path}")

    raw_name = raw.get("name")
    raw_description = raw.get("description")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    directory_name = path.rstrip("/").split("/")[-2]
    if not name or not _SKILL_NAME_RE.fullmatch(name) or name != directory_name:
        raise SkillCatalogError(f"Skill 名称必须为 kebab-case 且与目录一致：{path}")
    if not description or len(description) > 1024:
        raise SkillCatalogError(f"Skill description 必填且不能超过 1024 字符：{path}")

    raw_allowed_tools = raw.get("allowed-tools")
    if raw_allowed_tools is None:
        allowed_tools: frozenset[str] = frozenset()
    elif isinstance(raw_allowed_tools, str):
        allowed_tools = frozenset(part.strip(",") for part in raw_allowed_tools.split() if part.strip(","))
    else:
        raise SkillCatalogError(f"allowed-tools 必须是空格分隔字符串：{path}")

    raw_metadata = raw.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise SkillCatalogError(f"metadata 必须是对象：{path}")
    enforcement = raw_metadata.get("enforcement")
    if enforcement is not None and enforcement not in {"required"}:
        raise SkillCatalogError(f"不支持的 metadata.enforcement={enforcement!r}：{path}")
    if enforcement == "required" and not allowed_tools:
        raise SkillCatalogError(f"required Skill 必须声明 allowed-tools：{path}")

    unknown_tools = allowed_tools - available_tools
    if unknown_tools:
        raise SkillCatalogError(f"Skill 引用了当前 Agent 不存在的工具 {sorted(unknown_tools)}：{path}")

    return SkillDefinition(
        name=name,
        description=description,
        path=path,
        content=content,
        allowed_tools=allowed_tools,
        enforcement=str(enforcement) if enforcement is not None else None,
    )
