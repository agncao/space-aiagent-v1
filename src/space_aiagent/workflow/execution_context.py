"""单个 V2 步骤的工具权限与无进展保护上下文。"""

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class StepExecutionLimitError(RuntimeError):
    pass


class StepAlreadyCompletedError(RuntimeError):
    """模型在完成动作后仍尝试重复相同调用。"""

    def __init__(self, tool_name: str, result: dict[str, Any]) -> None:
        super().__init__(f"完成工具 {tool_name} 已成功执行，步骤强制结束")
        self.tool_name = tool_name
        self.result = result


@dataclass
class StepExecutionContext:
    run_id: str
    step_id: str
    execution_id: str
    allowed_tools: frozenset[str]
    completion_tools: frozenset[str]
    scene_revision: int
    scene_opened: bool
    max_tool_calls: int = 8
    tool_call_count: int = 0
    signature_counts: dict[str, int] = field(default_factory=dict)
    signature_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def signature(self, tool_name: str, args: dict[str, Any]) -> str:
        canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        raw = f"{tool_name}|{canonical}|{self.scene_revision}"
        return hashlib.sha256(raw.encode()).hexdigest()


step_execution_context_var: ContextVar[StepExecutionContext | None] = ContextVar(
    "step_execution_context_var", default=None
)
