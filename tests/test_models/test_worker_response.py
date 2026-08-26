"""WorkerResponse JSON Schema 测试。"""

import json

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from space_aiagent.models.response_schema.worker_response import ResponseCode, WorkerResponse


def test_response_code_descriptions_are_exposed_to_llm_tool_schema() -> None:
    tool_schema = convert_to_openai_tool(WorkerResponse)
    code_schema = tool_schema["function"]["parameters"]["properties"]["code"]

    assert code_schema["enum"] == [code.value for code in ResponseCode]
    for code in ResponseCode:
        assert f"{code.value}: {code.description}" in code_schema["description"]


def test_response_code_keeps_uppercase_string_values() -> None:
    assert ResponseCode.NO_SCENE == "NO_SCENE"
    assert ResponseCode.ZOOM_TO_SUCCESS == "ZOOM_TO_SUCCESS"
    assert WorkerResponse(status="info", code="SCENE_OPENED", summary="已打开").code is ResponseCode.SCENE_OPENED


def test_data_normalizes_json_encoded_list_from_tool_calling() -> None:
    encoded_data = json.dumps([{"scene_name": "场景0942_ 1个火箭"}], ensure_ascii=False)
    response = WorkerResponse(status="info", code="SCENE_QUERIED", summary="查询完成", data=encoded_data)
    assert response.data == [{"scene_name": "场景0942_ 1个火箭"}]


def test_data_normalizes_json_encoded_object_from_tool_calling() -> None:
    encoded_data = json.dumps({"entity_id": "facility-1"}, ensure_ascii=False)
    response = WorkerResponse(status="success", code="ENTITY_CREATED", summary="创建完成", data=encoded_data)
    assert response.data == {"entity_id": "facility-1"}


@pytest.mark.parametrize("invalid_data", ["null", "not-json", '"scalar"'])
def test_data_rejects_non_structured_json_strings(invalid_data: str) -> None:
    with pytest.raises(ValidationError):
        WorkerResponse(status="info", code="SCENE_QUERIED", summary="查询完成", data=invalid_data)


def test_data_treats_empty_string_as_none() -> None:
    """LLM 在无结构化数据时可能输出空串（实测案例：data=''），应归一为 None。

    空串若原样透传会触发 pydantic 联合类型校验失败，进而陷入
    langchain ToolStrategy 的结构化输出解析重试死循环。
    """
    response = WorkerResponse(
        status="success",
        code="ENTITIES_CLEARED",
        summary="已清除所有实体",
        data="",
    )
    assert response.data is None


def test_data_treats_whitespace_string_as_none() -> None:
    response = WorkerResponse(
        status="success",
        code="ENTITIES_CLEARED",
        summary="已清除所有实体",
        data="   ",
    )
    assert response.data is None
