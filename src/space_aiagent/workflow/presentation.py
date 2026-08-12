"""V2 Run 的前端投影，不复制持久化业务结果。"""

from typing import Any

from .models import WorkflowRun
from .result_resolver import InputBindingError, resolve_result_reference


def waiting_context_snapshot(run: WorkflowRun) -> dict[str, Any] | None:
    """将 waiting_context 序列化为前端可用的快照，并尝试解析关联的结果数据。

    返回 None 表示当前 run 不处于等待用户输入状态。
    返回 dict 时，除 waiting_context 原始字段外，额外包含一个 resolved_data 字段：
    - 如果 waiting_context 引用了前序步骤的结果（result_ref），则尝试解析该结果注入 payload
    - 解析失败（如依赖步骤尚未执行或结果不可用）时，resolved_data 为 None，不阻断快照生成
    """
    waiting = run.waiting_context
    if waiting is None:
        return None
    # 基础字段序列化（kind、step_id、prompt、data 等）
    payload = waiting.model_dump(mode="json")
    resolved_data: Any = None
    # 尝试解析 waiting_context 引用的前序步骤结果，供前端在等待提示中展示上下文
    if waiting.result_ref is not None:
        try:
            resolved_data = resolve_result_reference(run, waiting.result_ref, require_source_success=False)
        except InputBindingError:
            # 引用结果不可用时不阻断，前端按 resolved_data 为 None 处理即可
            resolved_data = None
    payload["resolved_data"] = resolved_data
    return payload


def workflow_run_snapshot(run: WorkflowRun) -> dict[str, Any]:
    payload = run.model_dump(mode="json")
    payload["waiting_context"] = waiting_context_snapshot(run)
    return payload
