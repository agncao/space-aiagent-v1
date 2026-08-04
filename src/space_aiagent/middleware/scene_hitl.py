"""
scene-agent Human-in-the-loop 中间件

"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command, interrupt

from space_aiagent.bridge import bridge_var
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.utils import string_util
from space_aiagent.tools.scene_management import tools

logger = get_logger(__name__)

# 与 read_tools._NAMESPACE 对齐（前端命名空间）
_SCENE_NAMESPACE = "scene_tools"
# open_scenario 返回码：当前场景存在未保存变更（来自前端 tool_result，不经 ResponseCode 枚举）
_CODE_UNSAVED_CHANGES = "SCENE_UNSAVED_CHANGES"
# >= 此数量触发场景选择中断
_MULTI_SELECT_THRESHOLD = 2


def _tool_message_of(result: Any) -> ToolMessage | None:
    """从 handler 返回值里取 ToolMessage。

    工具函数返回 Command(update={"messages": [ToolMessage, ...]})，少数路径可能
    直接返回 ToolMessage。统一收敛。
    """
    if isinstance(result, ToolMessage):
        return result
    update = getattr(result, "update", None) or {}
    for msg in update.get("messages", []):
        if isinstance(msg, ToolMessage):
            return msg
    return None


def _parse_content(tm: ToolMessage | None) -> dict[str, Any]:
    """解析 ToolMessage.content（JSON 字符串）为 dict，失败返回空 dict。"""
    if tm is None:
        return {}
    content = tm.content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(content, dict):
        return content
    return {}


def _scenario_list_of(result: Any) -> list[dict[str, Any]]:
    """从 query_scenario 的 Command update 里取 scenario_query_results。"""
    update = getattr(result, "update", None) or {}
    items = update.get("scenario_query_results")
    return items if isinstance(items, list) else []


def _replace_tool_message(result: Any, new_tm: ToolMessage) -> Any:
    """保留 Command 的其余 update（scenario_query_results/current_scene_name 等），
    只替换 messages 里的 ToolMessage。"""
    if not isinstance(result, Command):
        return new_tm
    update = dict(result.update)
    update["messages"] = [new_tm]
    return Command(update=update)


def _state_scene_name(request: ToolCallRequest) -> str | None:
    """从 request.state 读当前场景名（即将被切走的场景）。"""
    state = request.state
    if isinstance(state, dict):
        return state.get("current_scene_name")
    return getattr(state, "current_scene_name", None)


class SceneAgentHitlMiddleware(AgentMiddleware):
    """scene-agent 两个条件性 HITL 中断点的中间件驱动实现。

    挂载：scene-agent 的 middleware 列表（config/subagents.yaml 对应 agent），
    与 SubagentToolValidationMiddleware 串联。仅对 query_scenario/open_scenario
    两个工具生效，其余工具原样放行。
    """

    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        tool_name = request.tool_call.get("name", "")
        tool_call_id = request.tool_call.get("id", "")
        thread_id = get_config().get("configurable", {}).get("thread_id", "")

        # 先执行工具，拿到结果再判断是否需要中断
        # 注意：若命中条件触发 interrupt()，resume 时本节点会从头重跑，此处的
        # handler 会再执行一次（见模块 docstring 的幂等性说明）。
        result = await handler(request)

        if tool_name == tools.query_scenario.name:
            return await self._handle_query_select(result, tool_call_id, thread_id)

        if tool_name == tools.open_scenario.name:
            return await self._handle_open_unsaved(result, request, tool_call_id, thread_id)

        return result

    # ------------------------------------------------------------------
    # 中断点 1：query_scenario 多场景选择
    # ------------------------------------------------------------------
    async def _handle_query_select(
        self,
        result: Any,
        tool_call_id: str,
        thread_id: str,
    ) -> Any:
        scenarios = _scenario_list_of(result)
        if len(scenarios) < _MULTI_SELECT_THRESHOLD:
            # 0 或 1 个：不中断，原样返回（LLM 自行决定打开或告知未找到）
            return result

        candidates = [
            {
                "scene_name": s.get("scene_name"),
                "update_time": s.get("update_time", ""),
                "uploader_name": s.get("uploader_name", ""),
            }
            for s in scenarios
            if isinstance(s, dict) and s.get("scene_name")
        ]
        names = [c["scene_name"] for c in candidates]

        logger.info(
            "HITL scene_select 触发（多场景匹配）",
            thread_id=thread_id,
            count=len(candidates),
            candidates=names,
        )

        # interrupt()：首次执行暂停图；resume 时节点重跑，本调用返回 resume payload
        decision = interrupt(
            {
                "is_custom": True,
                "interrupt_type": "hitl_select",
                "message": "找到多个匹配场景，请选择要打开的场景：",
                "data": {"scene_info_list":candidates},
            }
        )

        selected = ""
        if isinstance(decision, dict):
            selected = str(decision.get("scene_name") or "").strip()

        if not selected:
            # resume payload 缺字段：放行原始结果，让 LLM 兜底（不应发生）
            logger.warning("HITL scene_select resume 缺 scene_name", thread_id=thread_id)
            return result

        logger.info("HITL scene_select 完成", thread_id=thread_id, selected=selected)

        # 把用户选择写回 ToolMessage，提示 LLM 打开选中场景（其余 update 保留）
        original = _parse_content(_tool_message_of(result))
        original.update(
            {
                "selected_scene": selected,
                "message": f"用户已从多个匹配场景中选择：{selected}，请打开该场景。",
            }
        )
        new_tm = ToolMessage(
            content=json.dumps(original, ensure_ascii=False),
            tool_call_id=tool_call_id,
        )
        return _replace_tool_message(result, new_tm)

    # ------------------------------------------------------------------
    # 中断点 2：open_scenario 未保存变更确认
    # ------------------------------------------------------------------
    async def _handle_open_unsaved(
        self,
        result: Any,
        request: ToolCallRequest,
        tool_call_id: str,
        thread_id: str,
    ) -> Any:
        code = _parse_content(_tool_message_of(result)).get("code", "")
        if code != _CODE_UNSAVED_CHANGES:
            # 成功 / SCENE_NOT_FOUND / 其它：不中断，原样返回
            return result

        args = request.tool_call.get("args", {}) or {}
        target_scene = args.get("scene_name") or args.get("sceneName") or ""
        current_scene = _state_scene_name(request) or ""

        logger.info(
            "HITL save_confirm 触发（当前场景有未保存变更）",
            thread_id=thread_id,
            current_scene=current_scene,
            target_scene=target_scene,
        )

        decision = interrupt(
            {
                "is_custom": True,
                "interrupt_type": "hitl_yn",
                "message": "当前场景存在未保存的变更，是否在切换前保存？(Y/N)",
                "data": {
                    "scene_name": current_scene,
                    "target_scene_name": target_scene,
                },
            }
        )

        save_on_change = False
        if isinstance(decision, dict):
            save_on_change = bool(decision.get("save_on_change"))

        logger.info(
            "HITL save_confirm 完成",
            thread_id=thread_id,
            save_on_change=save_on_change,
        )

        # 带用户决策重试 open_scenario（直接走 bridge，等价于工具内部那次 send_tool_call）
        bridge = bridge_var.get()
        if bridge is None:
            logger.error("HITL save_confirm 重试失败：bridge 未注入", thread_id=thread_id)
            return result

        retry_args: dict[str, Any] = {
            "sceneName": target_scene,
            "isSaveOnChange": save_on_change,
        }
        final = await bridge.send_tool_call(
            namespace=_SCENE_NAMESPACE,
            tool_func=string_util.snake_to_camel(tools.open_scenario.name),  # openScenario
            args=retry_args,
        )

        # 用最终结果替换原 ToolMessage；成功时同步 current_scene_name（与工具一致）
        update: dict[str, Any] = {
            "messages": [
                ToolMessage(
                    content=json.dumps(final, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                )
            ]
        }
        if isinstance(final, dict) and final.get("success"):
            resolved = final.get("current_scene_name")
            if resolved:
                update["current_scene_name"] = resolved
        return Command(update=update)
