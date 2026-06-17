"""B 方案：AgentResponse 状态归一化单测

normalize() 按 code 强制 status 一致，防止 LLM 偶发漂移。
漂移时返回新对象 + log warning（便于监控漂移频率）。
"""

import logging

from space_aiagent.bridge.response_renderer import normalize
from space_aiagent.models.response_schema import AgentResponse


def test_normalize_corrects_status_mismatch_for_known_code():
    """LLM 把 NO_SCENE 标成 info → normalize 改回 error"""
    resp = AgentResponse(
        status="info",
        code="NO_SCENE",
        summary="x",
    )

    out = normalize(resp)

    assert out.status == "error"
    assert out.code == "NO_SCENE"


def test_normalize_noop_when_status_already_correct():
    """status 已匹配 → 原样返回，不警告"""
    resp = AgentResponse(
        status="error",
        code="NO_SCENE",
        summary="x",
    )

    out = normalize(resp)

    assert out.status == "error"
    assert out is resp


def test_normalize_noop_for_unknown_code():
    """未知 code（如未来 LLM 自造的）→ 不动 status，避免误伤"""
    resp = AgentResponse(
        status="info",
        code="SOMETHING_NEW",
        summary="x",
    )

    out = normalize(resp)

    assert out.status == "info"
    assert out is resp


def test_normalize_logs_warning_on_drift(caplog):
    """漂移时记 warning，便于监控漂移频率"""
    resp = AgentResponse(
        status="info",
        code="NO_SCENE",
        summary="x",
    )

    with caplog.at_level(logging.WARNING, logger="space_aiagent.bridge.response_renderer"):
        normalize(resp)

    assert any("NO_SCENE" in rec.message and "info" in rec.message and "error" in rec.message
               for rec in caplog.records)


def test_normalize_covers_all_known_codes():
    """所有已知 code 都能在 _CODE_STATUS_MAP 中查到"""
    cases = [
        ("NO_SCENE", "error"),
        ("SCENE_CREATED", "success"),
        ("ENTITIES_EMPTY", "info"),
        ("ENTITIES_LIST", "success"),
        ("ENTITY_ADDED", "success"),
        ("ALL_ENTITIES_CLEARED", "success"),
    ]
    for code, expected_status in cases:
        # 用一个肯定不匹配的 status 触发归一化，验证 map 命中
        wrong_status = "confirm" if expected_status != "confirm" else "info"
        resp = AgentResponse(status=wrong_status, code=code, summary="x")
        out = normalize(resp)
        assert out.status == expected_status, f"{code} 期望归一化为 {expected_status}，实际 {out.status}"
