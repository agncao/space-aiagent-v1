"""Skill Catalog 协议与质量门槛测试。"""

from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend

from space_aiagent.infrastructure.skill.catalog import SkillCatalog, SkillCatalogError


def _write_skill(
    root: Path,
    source: str,
    name: str,
    *,
    description: str = "测试工作流",
    allowed_tools: str | None = "tool_a",
    enforcement: str | None = "required",
) -> None:
    directory = root / source / name
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if allowed_tools is not None:
        lines.append(f"allowed-tools: {allowed_tools}")
    if enforcement is not None:
        lines.extend(["metadata:", f"  enforcement: {enforcement}"])
    lines.extend(["---", "", "# 测试"])
    (directory / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _backend(root: Path) -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(root), virtual_mode=True)


def test_builtin_skills_have_valid_required_tool_contract():
    backend = FilesystemBackend(root_dir="src/space_aiagent/skills", virtual_mode=True)
    scene = SkillCatalog.from_backend(
        backend,
        ["/scene/"],
        {"query_scenario", "open_scenario", "create_scenario", "rename_scenario", "delete_scene"},
    )
    entity = SkillCatalog.from_backend(
        backend,
        ["/entity/"],
        {
            "add_point_entity",
            "create_sgp4_orbit",
            "query_entities",
            "zoom_to",
            "delete_entities",
            "update_sgp4_orbit",
        },
    )
    analysis = SkillCatalog.from_backend(
        backend,
        ["/analysis/"],
        {"query_analysis_item", "analyze_entity_data"},
    )

    assert scene.names == {"open-scenario", "query-scenario"}
    assert scene.governed_tools == {"query_scenario", "open_scenario"}
    assert entity.names == {"add-entity", "delete-entities", "zoom-to"}
    assert entity.governed_tools == {
        "add_point_entity",
        "create_sgp4_orbit",
        "delete_entities",
        "zoom_to",
    }
    assert analysis.names == {"analyze-entity-data"}
    assert analysis.governed_tools == {"query_analysis_item", "analyze_entity_data"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"description": ""}, "description"),
        ({"allowed_tools": None}, "allowed-tools"),
        ({"allowed_tools": "missing_tool"}, "不存在的工具"),
        ({"enforcement": "optional"}, "不支持"),
    ],
)
def test_invalid_required_skill_fails_fast(tmp_path: Path, kwargs: dict, message: str):
    _write_skill(tmp_path, "scene", "bad-skill", **kwargs)
    with pytest.raises(SkillCatalogError, match=message):
        SkillCatalog.from_backend(_backend(tmp_path), ["/scene/"], {"tool_a"})


def test_duplicate_skill_name_across_sources_fails(tmp_path: Path):
    _write_skill(tmp_path, "first", "same-skill")
    _write_skill(tmp_path, "second", "same-skill")
    with pytest.raises(SkillCatalogError, match="名称重复"):
        SkillCatalog.from_backend(_backend(tmp_path), ["/first/", "/second/"], {"tool_a"})


def test_multiple_skills_may_share_a_tool(tmp_path: Path):
    _write_skill(tmp_path, "scene", "first-skill")
    _write_skill(tmp_path, "scene", "second-skill")
    catalog = SkillCatalog.from_backend(_backend(tmp_path), ["/scene/"], {"tool_a"})
    assert catalog.governed_tools == {"tool_a"}
