"""Agent state schema

扩展 deepagents 的 DeepAgentState，加入航天 domain 字段。

利用 deepagents `task` 工具的双向自动同步：
- 父→子：subagents.py:_validate_and_prepare_state 复制非 _EXCLUDED_STATE_KEYS 字段
- 子→父：subagents.py:_return_command_with_state_update 回传非 _EXCLUDED_STATE_KEYS 字段

替代 ContextVar 跨 task 边界的隔离问题（LangGraph 每个 node 用
copy_context() + asyncio.create_task(context=...) 隔离运行，
ContextVar 不跨 task 边界传播）。
"""
from typing import NotRequired

from deepagents.graph import DeepAgentState


class SpaceAgentState(DeepAgentState):
    """航天 domain state schema"""

    current_scene_name: NotRequired[str | None]
