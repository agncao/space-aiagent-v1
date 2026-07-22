"""Agent state schema

扩展 deepagents 的 DeepAgentState，加入航天 domain 字段。

利用 deepagents `task` 工具的双向自动同步：
- 父→子：subagents.py:_validate_and_prepare_state 复制非 _EXCLUDED_STATE_KEYS 字段
- 子→父：subagents.py:_return_command_with_state_update 回传非 _EXCLUDED_STATE_KEYS 字段

替代 ContextVar 跨 task 边界的隔离问题（LangGraph 每个 node 用
copy_context() + asyncio.create_task(context=...) 隔离运行，
ContextVar 不跨 task 边界传播）。
"""

from typing import Annotated, Any, NotRequired

from deepagents.graph import DeepAgentState


def update_tool_result(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """去重追加同一步产生的并发工具结果。"""
    if right is None:
        return None
    merged = list(left or [])
    merged.extend(result for result in right if result not in merged)
    return merged


def _keep_last_scene_name(left: str | None, right: str | None) -> str | None:
    """current_scene_name 并发写入时取最后一个（last-write-wins）。"""
    return right


class SpaceAgentState(DeepAgentState):
    """航天 domain state schema"""

    current_scene_name: NotRequired[Annotated[str | None, _keep_last_scene_name]]
    # 仅保存白名单展示字段；并发结果由 Reducer 去重追加。
    scenario_query_results: NotRequired[Annotated[list[dict[str, Any]] | None, update_tool_result]]
