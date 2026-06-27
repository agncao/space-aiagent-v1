"""
场景管理 — 写工具

直接执行，无前后置流程。
"""
import inspect
from langchain_core.tools import tool
from space_aiagent.bridge import bridge_var, current_scene_name_var
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.schemas import ScenarioConfig

import logging

logger = logging.getLogger(__name__)

@tool(args_schema=ScenarioConfig)
async def create_scenario(
    scene_name: str = "新建场景",
    central_body: str = "Earth",
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
) -> dict:
    """
    创建场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(create_scenario,locals())

    result  = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    logger.debug(f"create_scenario result: {result}, data: {result.get('data')}")
    if result["success"]:
        data : dict =result.get("data") or {}
        current_scene_name_var.set(data.get("scene_name"))
        logger.debug(f"create_scenario success, scene_name: {current_scene_name_var.get()}")
    return result


@tool
async def rename_scenario(scene_name: str) -> dict:
    """
    重命名/修改当前场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(rename_scenario,locals())

    result = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    if result["success"]:
        data : dict =result.get("data") or {}
        current_scene_name_var.set(data.get("scene_name"))
    return result


@tool
async def delete_scene() -> dict:
    """
    删除当前场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name

    result  = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args={},
    )
    if result["success"]:
        current_scene_name_var.set(None)
    return result
