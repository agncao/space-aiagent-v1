"""场景实体数据分析工具。"""

import inspect
from typing import Literal

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.response_schema.worker_response import ResponseCode
from space_aiagent.tools.contracts import workflow_tool

_NAMESPACE: str = "data_analyse_tools"


@workflow_tool(requires={"scene.opened"})
@tool
async def analyze_entity_data(
    analysis_name: str,
    is_show: bool = True,
    show_kind: Literal["Report", "Graph"] = "Report",
) -> dict:
    """分析实体的指定数据分析项，在航天场景内显示或隐藏其数据分析结果。

    Args:
        analysis_name: 要分析的数据分析项名称，例如“姿态四元数”、“经典瞬时根数”或“光照时间”。
        is_show: True 表示显示分析结果（默认），False 表示隐藏已显示的分析结果。
        show_kind: 数据在航天场景内的展示方式：
            - `Report`：表格、表单、数据报表
            - `Graph`：图表

    Returns:
        完成数据分析并在航天场景内显示（或隐藏）结果后的执行结果。
    """
    if not analysis_name or not analysis_name.strip():
        return {
            "success": False,
            "code": ResponseCode.MISSING_ARGUMENTS,
            "data": None,
            "message": "待分析的数据项名称不能为空",
        }

    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(analyze_entity_data, locals())

    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    return result


@workflow_tool(requires={"scene.opened"})
@tool
async def query_analysis_item(analysis_name: str = "", show_kind: Literal["Report", "Graph"] = "Report") -> dict:
    """按名称模糊匹配，查询当前实体可用的数据分析项。

    Args:
        analysis_name: 待匹配的数据项名称关键词，传空字符串表示查询全部数据项。
        show_kind: 按展示形式筛选，仅返回支持该形式的数据项：
            - `Report`：表格、表单、数据报表
            - `Graph`：图表

    Returns:
        匹配到的数据项名称及其支持的展示形式。
    """
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(query_analysis_item, locals())

    bridge = bridge_var.get()
    result = await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    return result
