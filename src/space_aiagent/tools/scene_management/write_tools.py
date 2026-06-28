"""
场景管理 — 写工具

直接执行，无前后置流程。

返回 Command 同时更新 state（current_scene_name）和 messages（ToolMessage），
利用 deepagents task 双向同步让 orchestrator 自动获得最新场景名。
"""
import inspect
import json
import logging

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.schemas import ScenarioConfig

logger = logging.getLogger(__name__)


def _build_command(
    result: dict,
    runtime: ToolRuntime,
    scene_name: str | None = None,
    clear_scene: bool = False,
) -> Command:
    """根据 bridge 返回值构造 Command

    Args:
        result: bridge.send_tool_call 返回的 dict
        runtime: langgraph 注入的 ToolRuntime（拿 tool_call_id）
        scene_name: 成功时要写入 state 的新场景名
        clear_scene: True 时把 current_scene_name 置为 None（如 delete_scene 成功）
    """
    update: dict = {
        "messages": [
            ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=runtime.tool_call_id,
            )
        ]
    }
    if clear_scene:
        update["current_scene_name"] = None
    elif scene_name is not None:
        update["current_scene_name"] = scene_name
    return Command(update=update)


@tool(args_schema=ScenarioConfig)
async def create_scenario(
    runtime: ToolRuntime,
    scene_name: str = "新建场景",
    central_body: str = "Earth",
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
) -> Command:
    """
    创建场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(create_scenario, locals())

    result = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    if result["success"]:
        data: dict = result.get("data") or {}
        scene_name_resolved = data.get("scene_name")
        logger.debug("create_scenario success, scene_name: %s", scene_name_resolved)
        return _build_command(result, runtime, scene_name=scene_name_resolved)
    return _build_command(result, runtime)


@tool
async def rename_scenario(runtime: ToolRuntime, scene_name: str) -> Command:
    """
    重命名/修改当前场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name
    args: dict = string_util.args_to_camel(rename_scenario, locals())

    result = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    if result["success"]:
        data: dict = result.get("data") or {}
        scene_name_resolved = data.get("scene_name")
        return _build_command(result, runtime, scene_name=scene_name_resolved)
    return _build_command(result, runtime)


@tool
async def delete_scene(runtime: ToolRuntime) -> Command:
    """
    删除当前场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name

    result = await bridge.send_tool_call(
        tool_func=string_util.snake_to_camel(tool_func),
        args={},
    )
    if result["success"]:
        # 清除场景名（None 表示无场景）
        return _build_command(result, runtime, clear_scene=True)
    return _build_command(result, runtime)
