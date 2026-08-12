"""
场景管理 — 写工具

直接执行，无前后置流程。

返回 Command 只更新 messages（ToolMessage）。执行器根据已持久化的工具回告更新
WorkflowRun.scene_context。
"""

import inspect
import json

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.models.biz_schemas import ScenarioConfig

logger = get_logger(__name__)

_NAMESPACE = "scene_tools"


def _build_command(
    result: dict,
    runtime: ToolRuntime,
) -> Command:
    """把前端工具回告写入 Worker 消息上下文。"""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=runtime.tool_call_id,
                )
            ]
        }
    )


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
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
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
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args=args,
    )
    return _build_command(result, runtime)


@tool
async def delete_scene(runtime: ToolRuntime) -> Command:
    """
    删除当前场景。
    """
    bridge = bridge_var.get()
    tool_func = inspect.currentframe().f_code.co_name

    result = await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func=string_util.snake_to_camel(tool_func),
        args={},
    )
    return _build_command(result, runtime)
