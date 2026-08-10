"""AgentResponse JSON Schema 测试。"""

import json

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

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


def test_data_normalizes_json_encoded_list_from_tool_calling() -> None:
    """兼容模型把 data 数组二次序列化为字符串，但对外仍保持列表类型。"""
    encoded_data = json.dumps([{"scene_name": "场景0942_ 1个火箭"}], ensure_ascii=False)

    response = AgentResponse(
        status="info",
        code="SCENE_QUERIED",
        summary="查询完成",
        data=encoded_data,
    )

    assert response.data == [{"scene_name": "场景0942_ 1个火箭"}]


@pytest.mark.parametrize("invalid_data", ['{"scene_name": "场景1"}', "null", "not-json"])
def test_data_rejects_non_list_json_strings(invalid_data: str) -> None:
    """只修复合法数组的二次序列化，不吞掉错误结构或非法 JSON。"""
    with pytest.raises(ValidationError):
        AgentResponse(
            status="info",
            code="SCENE_QUERIED",
            summary="查询完成",
            data=invalid_data,
        )
