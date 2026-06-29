from space_aiagent.models.response_schema.agent_struct_response import ResponseCode

# 前置条件不满足的 code，触发意图捕获
INTENTION_TO_CATCH_CODES = frozenset({ResponseCode.NO_SCENE})


# 前置条件已满足的 code，触发自动续接（默认值，可构造时覆盖）
INTENTION_RESUME_TRIGGER_CODES = frozenset({ResponseCode.SCENE_CREATED})
