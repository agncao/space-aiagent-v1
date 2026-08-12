from space_aiagent.infrastructure.response_template_yaml import DEFAULT_TEMPLATES
from space_aiagent.models.response_schema.worker_response import ResponseCode, WorkerResponse

# shortcut key → 预构建 WorkerResponse
# 新增确定性 case 只需在此追加一条
SHORTCUT_RESPONSES: dict[str, WorkerResponse] = {
    ResponseCode.NO_SCENE: WorkerResponse(
        status="info",
        code=ResponseCode.NO_SCENE,
        summary=DEFAULT_TEMPLATES[ResponseCode.NO_SCENE],
    ),
    ResponseCode.LLM_UNAVAILABLE: WorkerResponse(
        status="error",
        code=ResponseCode.LLM_UNAVAILABLE,
        summary=DEFAULT_TEMPLATES[ResponseCode.LLM_UNAVAILABLE],
    ),
    ResponseCode.SKILL_ROUTING_FAILED: WorkerResponse(
        status="error",
        code=ResponseCode.SKILL_ROUTING_FAILED,
        summary=DEFAULT_TEMPLATES[ResponseCode.SKILL_ROUTING_FAILED],
    ),
}
