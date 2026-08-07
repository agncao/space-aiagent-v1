from space_aiagent.infrastructure.response_template_yaml import DEFAULT_TEMPLATES
from space_aiagent.models.response_schema.agent_struct_response import AgentResponse, ResponseCode

# 前置条件不满足的 code，触发意图捕获
INTENTION_TO_CATCH_CODES = frozenset({ResponseCode.NO_SCENE})


# 前置条件已满足的 code，触发自动续接（默认值，可构造时覆盖）
INTENTION_RESUME_TRIGGER_CODES = frozenset({ResponseCode.SCENE_CREATED})


# shortcut key → 预构建 AgentResponse
# 新增确定性 case 只需在此追加一条
SHORTCUT_RESPONSES: dict[str, AgentResponse] = {
    ResponseCode.NO_SCENE: AgentResponse(
        status="info",
        code=ResponseCode.NO_SCENE,
        summary=DEFAULT_TEMPLATES[ResponseCode.NO_SCENE],
        suggestions=[],
    ),
    ResponseCode.TASK_LOOP_GUARD: AgentResponse(
        status="confirm",
        code=ResponseCode.TASK_LOOP_GUARD,
        summary=DEFAULT_TEMPLATES[ResponseCode.TASK_LOOP_GUARD],
        suggestions=[],
    ),
    ResponseCode.LLM_UNAVAILABLE: AgentResponse(
        status="error",
        code=ResponseCode.LLM_UNAVAILABLE,
        summary=DEFAULT_TEMPLATES[ResponseCode.LLM_UNAVAILABLE],
        suggestions=[],
    ),
    ResponseCode.SKILL_ROUTING_FAILED: AgentResponse(
        status="error",
        code=ResponseCode.SKILL_ROUTING_FAILED,
        summary=DEFAULT_TEMPLATES[ResponseCode.SKILL_ROUTING_FAILED],
        suggestions=[],
    ),
}
