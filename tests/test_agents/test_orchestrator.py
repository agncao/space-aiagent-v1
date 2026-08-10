"""Orchestrator 构建测试。"""

from space_aiagent.agents.orchestrator import _build_system_prompt


def test_system_prompt_renders_agent_response_data_examples() -> None:
    """JSON 示例中的花括号不应被 str.format 当成模板变量。"""
    prompt = _build_system_prompt([{"name": "scene-agent", "description": "管理场景"}])

    assert '- data: `[{"capability": "<用户想要的能力简述>"}]`' in prompt
    assert '`data` 必须直接传 JSON 数组，例如 `[{"scene_name": "示例场景"}]`' in prompt
    assert '"data": "[{\\"scene_name\\": \\"示例场景\\"}]"' in prompt


def test_system_prompt_requires_continuing_compound_task_delegation() -> None:
    """复合任务在所有子任务终结前不得提前输出最终响应。"""
    prompt = _build_system_prompt(
        [
            {"name": "scene-agent", "description": "管理场景"},
            {"name": "entity-agent", "description": "管理实体"},
        ]
    )

    assert "必须拆成多个 `task` 依次委派" in prompt
    assert "只要还有可执行的未完成子任务，就禁止调用 `AgentResponse`" in prompt
    assert "scene-agent 成功返回后，必须第二次调用 task" in prompt
    assert "subagent_type: entity-agent" in prompt
    assert "不要重复打开场景" in prompt
