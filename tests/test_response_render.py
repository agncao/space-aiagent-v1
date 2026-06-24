"""ResponseRenderer.render() 单测

启用模板匹配后，render() 应该：
- 命中模板 + args 字段齐全 → 用模板渲染
- 命中模板 + args=None + tool_history 有匹配记录 → 从 tool_history 补字段后用模板渲染
- 命中模板 + 缺字段（tool_history 也补不到）→ 降级用 summary（不再保留 {var} 占位符）
- 未命中模板 → 用 summary
- 不自动追加 suggestions 列表（避免与模板内文本重复）

注：status/args 的稳定化已由 ResponseStabilizationMiddleware 在 agent 流程内
完成，本测试聚焦 render() 自身的纯渲染行为。
"""

from space_aiagent.bridge import tools_results_var
from space_aiagent.bridge.response_renderer import ResponseRenderer
from space_aiagent.models.response_schema import AgentResponse


def test_render_uses_template_for_known_code():
    """命中模板 → 用模板渲染，summary 被忽略"""
    response = AgentResponse(
        status="success",
        code="SCENE_CREATED",
        summary="某 LLM 写的简短摘要",
        args={"scene_name": "测试场景"},
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    assert "测试场景" in result  # args 填充
    assert "已创建成功" in result  # 模板文本
    assert "某 LLM 写的简短摘要" not in result  # summary 被忽略


def test_render_fills_placeholders_from_args():
    """模板 {key} 从 args 取值（snake_case 与模板对齐）"""
    response = AgentResponse(
        status="success",
        code="ENTITIES_LIST",
        summary="x",
        args={"scene_name": "MY_SCENE", "count": 5, "entities": "SAT1\nSAT2"},
    )
    renderer = ResponseRenderer()

    result = renderer.render(response)

    assert "MY_SCENE" in result
    assert "5" in result
    assert "SAT1" in result
    assert "SAT2" in result


def test_render_supplements_args_from_tool_history_when_args_none():
    """args=None（LLM 没填）+ tool_history 有匹配 code 的记录 → 从 data 补占位符

    复现 SCENE_CREATED 生产 case：LLM 只填了 code/summary/status，args 默认 None。
    上一层的 create_scenario 工具记录在 tools_results_var 里，data.scene_name 可补全模板。
    """
    response = AgentResponse(
        status="success",
        code="SCENE_CREATED",
        summary="某 LLM 摘要",
        args=None,
    )
    renderer = ResponseRenderer()

    tool_record = {
        "status": "success",
        "code": "SCENE_CREATED",
        "summary": "场景创建成功",
        "args": {},
        "tool_func": "create_scenario",
        "data": {"scene_name": "新建场景"},
    }
    token = tools_results_var.set([tool_record])
    try:
        result = renderer.render(response)
    finally:
        tools_results_var.reset(token)

    assert "新建场景" in result
    assert "已创建成功" in result
    assert "某 LLM 摘要" not in result  # 模板命中，summary 被忽略


def test_render_falls_back_to_summary_when_arg_missing():
    """args 缺字段且 tool_history 也补不到 → 不再保留 {var} 占位符，降级用 summary"""
    response = AgentResponse(
        status="success",
        code="SCENE_CREATED",
        summary="降级文案",
        args={},  # 缺 scene_name
    )
    renderer = ResponseRenderer()

    token = tools_results_var.set([])  # 空 tool_history
    try:
        result = renderer.render(response)
    finally:
        tools_results_var.reset(token)

    assert result == "降级文案"
    assert "{scene_name}" not in result


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
        args={"scene_name": "x"},
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
        "SCENE_CREATED",
        broken_template,
    )

    result = renderer.render(response)

    assert result == "降级文案"
