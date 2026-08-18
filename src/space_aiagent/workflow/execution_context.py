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


# 这是一个单步骤执行期间的上下文容器，通过ContextVar 在线程/协程间传递，生命周期与单个 PlanStep 的执行绑定。它的核心职责有三：
#
# 工具权限控制 — 限制当前步骤只能调用哪些工具
# 无进展循环保护 — 防止 LLM 反复调用相同工具、死循环消耗 token
# 幂等去重 — 工具调用成功后缓存结果，防止 LLM 重复调用
# 例如：用户输入 "统计当前场景有多少实体"，Agent 规划出 query_entities 步骤：
#         step_execute_context = StepExecutionContext(
#             run_id="run_abc123",
#             step_id="step_001",
#             execution_id="exec_xyz",
#             allowed_tools=frozenset({"query_entities"}),        # 来自 action 定义，来自 config/actions.yaml
#             completion_tools=frozenset({"query_entities"}),     # 来自 action 定义, 来自 config/actions.yaml 完成标记工具
#             scene_revision=3,                                    # 当前场景版本号
#             scene_opened=True,                                   # 场景已打开
#         )
#     allowed_tools = frozenset({"query_entities"}) 用在 worker_tool_validation.py awrap_tool_call 方法：
#         if tool_name not in execution_context.allowed_tools:
#             return ToolMessage(content={"success": False, "code": "ACTION_TOOL_NOT_ALLOWED", ...})
#         实际效果：entity-agent 在执行 query_entities 步骤时，如果 LLM "自作主张" 调了 add_point_entity 或 delete_scene，直接返回错误，不允许越权。每个步骤只能调用当前 action 声明允许的工具。
#     completion_tools=frozenset({"query_entities"}) 也用在 worker_tool_validation.py awrap_tool_call 方法：
#         if tool_name in execution_context.completion_tools
#            and (payload := _extract_success_payload(result)) is not None:
#             execution_context.signature_results[execution_signature] = payload
#         实际效果：当 query_entities 工具调用成功返回后，结果被缓存到 signature_results。如果 LLM 再次尝试调用 query_entities 且参数相同，会被 signature_results 去重拦截
#             completion_tools 是 allowed_tools 的子集，标记哪些工具调用成功即代表步骤实质性完成。对比 open_scene action
#     signature_results  用在 worker_tool_validation.py awrap_tool_call 方法：
#         # 调用前检查：同一签名已经有结果了 → 直接抛异常阻止重复调用
#         if execution_signature in execution_context.signature_results:
#             raise StepAlreadyCompletedError(
#                 tool_name,
#                 execution_context.signature_results[execution_signature],
#             )
#
#         # 调用成功后缓存
#         execution_context.signature_results["a1b2c3..."] = {
#             "success": True,
#             "data": [{"entity_id": "1", "name": "卫星A"}, {"entity_id": "2", "name": "卫星B"}]
#         }
# 完整生命周期示例：当用户输入"统计有多少实体"
#   → Plan: query_entities 步骤
#   → Execute: 创建 StepExecutionContext
#       allowed_tools = {"query_entities"}
#       completion_tools = {"query_entities"}
#       scene_revision = 3
#       scene_opened = True
#
#   → LLM 第 1 次调用 query_entities({"scene_name": "当前场景"})
#       signature = sha256("query_entities|{...}|3") → "abc"
#       signature_counts = {"abc": 1}          ← 第 1 次，OK
#       工具执行成功 → 返回 10 个实体
#       signature_results = {"abc": {...}}     ← 缓存结果
#
#   → LLM 第 2 次调用 query_entities({"scene_name": "当前场景"})
#       signature = "abc" (相同)
#       signature_counts = {"abc": 2}          ← 第 2 次，OK（≤2）
#       signature_results 已有 "abc" → StepAlreadyCompletedError → DEDUPLICATED_SUCCESS
#
#   → 步骤结束，evidence 里带上 tool_result
@dataclass
class StepExecutionContext:
    run_id: str
    step_id: str
    execution_id: str
    # 工具白名单,来自 config/actions.yaml, 不允许LLM自作主张调用非白名单里的工具
    allowed_tools: frozenset[str]
    # 完成标记工具, 也来自 config/actions.yaml，completion_tools 是 allowed_tools 的子集，标记哪些工具调用成功即代表步骤实质性完成。对比 open_scene action
    completion_tools: frozenset[str]
    scene_revision: int
    scene_opened: bool
    max_tool_calls: int = 20
    tool_call_count: int = 0
    # 同参数调用计数 初始值：{}，如果 LLM 连续 3 次调用 同方法同参数，例如：query_entities({"scene_name": "当前场景"})，第 3 次直接抛异常终止步骤。防止 LLM "卡住" 反复尝试同一操作。
    signature_counts: dict[str, int] = field(default_factory=dict)
    # 成功结果缓存 初始值：{}，
    signature_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def signature(self, tool_name: str, args: dict[str, Any]) -> str:
        canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        raw = f"{tool_name}|{canonical}|{self.scene_revision}"
        return hashlib.sha256(raw.encode()).hexdigest()


step_execution_context_var: ContextVar[StepExecutionContext | None] = ContextVar(
    "step_execution_context_var", default=None
)
