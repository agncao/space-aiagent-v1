"""业务工具固有工作流契约。"""

from collections.abc import Callable
from typing import TypeVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

_WORKFLOW_METADATA_KEY = "space_workflow"
ToolT = TypeVar("ToolT", bound=BaseTool)


class WorkflowToolContract(BaseModel):
    requires: frozenset[str] = Field(default_factory=frozenset)
    effects: frozenset[str] = Field(default_factory=frozenset)
    invalidates: frozenset[str] = Field(default_factory=frozenset)


def workflow_tool(
    *,
    requires: set[str] | frozenset[str] | None = None,
    effects: set[str] | frozenset[str] | None = None,
    invalidates: set[str] | frozenset[str] | None = None,
) -> Callable[[ToolT], ToolT]:
    """给 ``@tool`` 产物附加命名空间化契约；必须写在 ``@tool`` 外层。"""
    contract = WorkflowToolContract(
        requires=frozenset(requires or set()),
        effects=frozenset(effects or set()),
        invalidates=frozenset(invalidates or set()),
    )

    def decorate(tool: ToolT) -> ToolT:
        metadata = dict(tool.metadata or {})
        metadata[_WORKFLOW_METADATA_KEY] = contract.model_dump(mode="json")
        tool.metadata = metadata
        return tool

    return decorate


def get_workflow_tool_contract(tool: BaseTool) -> WorkflowToolContract:
    metadata = tool.metadata or {}
    payload = metadata.get(_WORKFLOW_METADATA_KEY, {})
    return WorkflowToolContract.model_validate(payload)
