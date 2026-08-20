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


class StepNoSceneError(RuntimeError):
    """工具要求 scene.opened 但当前无场景，步骤确定性短路。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"工具 {tool_name} 需要先打开或新建场景")
        self.tool_name = tool_name


@dataclass
class StepExecutionContext:
    """一个 Worker Todo 执行期间的工具契约与无进展保护状态。"""

    run_id: str
    step_id: str
    # 当前 Worker 在 workers.yaml 中绑定的全部领域工具。
    allowed_tools: frozenset[str]
    scene_revision: int
    facts: frozenset[str] = field(default_factory=frozenset)
    max_tool_calls: int = 20
    tool_call_count: int = 0
    # 同参数调用计数；第三次相同调用视为无进展循环。
    signature_counts: dict[str, int] = field(default_factory=dict)
    # 成功结果缓存，防止模型重复执行已经成功的副作用。
    signature_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_requirements: dict[str, dict[str, Any]] = field(default_factory=dict)
    successful_tool_names: list[str] = field(default_factory=list)
    effects: set[str] = field(default_factory=set)
    invalidates: set[str] = field(default_factory=set)

    def signature(self, tool_name: str, args: dict[str, Any]) -> str:
        canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        raw = f"{tool_name}|{canonical}|{self.scene_revision}"
        return hashlib.sha256(raw.encode()).hexdigest()


step_execution_context_var: ContextVar[StepExecutionContext | None] = ContextVar(
    "step_execution_context_var", default=None
)
