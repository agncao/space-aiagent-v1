"""AgentResponse JSON Schema 测试。"""

from langchain_core.utils.function_calling import convert_to_openai_tool

from space_aiagent.models.response_schema.agent_struct_response import AgentResponse, ResponseCode


def test_response_code_descriptions_are_exposed_to_llm_tool_schema() -> None:
    """每个响应编码及其含义都必须出现在最终传给 LLM 的工具 schema 中。"""
    tool_schema = convert_to_openai_tool(AgentResponse)
    code_schema = tool_schema["function"]["parameters"]["properties"]["code"]

    assert code_schema["enum"] == [code.value for code in ResponseCode]
    for code in ResponseCode:
        assert f"{code.value}: {code.description}" in code_schema["description"]


def test_response_code_keeps_uppercase_string_values() -> None:
    """为成员增加描述后，不改变 WebSocket 和模板依赖的原有编码值。"""
    assert ResponseCode.NO_SCENE == "NO_SCENE"
    assert AgentResponse(status="info", code="SCENE_OPENED", summary="已打开").code is ResponseCode.SCENE_OPENED
