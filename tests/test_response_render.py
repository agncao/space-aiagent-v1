"""ResponseRenderer.render() 单测

启用模板匹配后，render() 应该：
- 命中模板 → 用 details 填充占位符
- 未命中 → 降级用 summary
- 不自动追加 suggestions 列表（避免与模板内文本重复）
"""

from space_aiagent.bridge.response_renderer import ResponseRenderer
from space_aiagent.models.response_schema import AgentResponse


def test_render_uses_template_for_known_code():
    """命中模板 → 用模板渲染，summary 被忽略"""
    response = AgentResponse(
        status="success",
        code="SCENE_CREATED",
        summary="某 LLM 写的简短摘要",
        details={"sceneName": "测试场景"},
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    assert "测试场景" in result  # details 填充
    assert "已创建成功" in result  # 模板文本
    assert "某 LLM 写的简短摘要" not in result  # summary 被忽略


def test_render_fills_placeholders_from_details():
    """模板 {key} 从 details 取值"""
    response = AgentResponse(
        status="success",
        code="ENTITIES_LIST",
        summary="x",
        details={"sceneName": "MY_SCENE", "count": 5, "entity_list": "SAT1\nSAT2"},
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    assert "MY_SCENE" in result
    assert "5" in result
    assert "SAT1" in result
    assert "SAT2" in result


def test_render_keeps_placeholder_when_detail_missing():
    """details 缺失 → 保留 {key} 占位符，不抛 KeyError"""
    response = AgentResponse(
        status="success",
        code="SCENE_CREATED",
        summary="x",
        details={},  # 缺 sceneName
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    assert "{sceneName}" in result


def test_render_falls_back_to_summary_for_unknown_code():
    """未知 (status, code) → 用 summary"""
    response = AgentResponse(
        status="info",
        code="SOMETHING_NEW",
        summary="LLM 自由生成的回复",
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    assert result == "LLM 自由生成的回复"


def test_render_does_not_auto_append_suggestions():
    """suggestions 不自动追加为列表（避免与模板内引导文本重复）"""
    response = AgentResponse(
        status="error",
        code="NO_SCENE",
        summary="x",
        suggestions=["UNIQUE_SUGGESTION_A", "UNIQUE_SUGGESTION_B"],
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    # suggestions 字段不应被自动拼到输出里
    assert "UNIQUE_SUGGESTION_A" not in result
    assert "UNIQUE_SUGGESTION_B" not in result


def test_render_falls_back_to_summary_when_template_format_fails(monkeypatch):
    """模板渲染过程抛异常 → 降级用 summary（健壮性）"""
    response = AgentResponse(
        status="success",
        code="SCENE_CREATED",
        summary="降级文案",
        details={"sceneName": "x"},
    )
    renderer = ResponseRenderer()

    # 故意把模板替换成一个会抛异常的 format_map
    broken_template = type(
        "BadTemplate",
        (),
        {"format_map": lambda self, d: (_ for _ in ()).throw(ValueError("boom"))},
    )()
    monkeypatch.setitem(
        renderer._templates,
        ("success", "SCENE_CREATED"),
        broken_template,
    )

    result = renderer.render(response)

    assert result == "降级文案"
