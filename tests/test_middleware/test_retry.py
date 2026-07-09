"""RetryMiddleware 单测（Task 3 先测降级出口 shortcut 存在）"""

from space_aiagent.models.response_schema import response_constants, response_util
from space_aiagent.models.response_schema.agent_struct_response import ResponseCode


def test_llm_unavailable_shortcut_exists():
    """SHORTCUT_RESPONSES 含 llm_unavailable，code=LLM_UNAVAILABLE，render 非空"""
    shortcut = response_constants.SHORTCUT_RESPONSES["llm_unavailable"]
    assert shortcut.code == ResponseCode.LLM_UNAVAILABLE
    assert shortcut.status == "error"
    text = response_util.render(shortcut)
    assert len(text) > 0
