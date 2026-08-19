"""显式解析步骤之间的结果绑定。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.workflow_schemas import PlanStep, ResultRef, StepStatus, WorkflowRun


logger = get_logger(__name__)

class InputBindingError(ValueError):
    """ResultRef 无法解析为步骤输入。"""


_MISSING = object()


def validate_json_pointer(pointer: str) -> None:
    """校验 RFC 6901 基本语法，特别拒绝非法 `~` 转义。"""
    if pointer == "":
        return
    if not pointer.startswith("/"):
        raise InputBindingError(f"非法 JSON Pointer: {pointer}")
    for token in pointer[1:].split("/"):
        index = 0
        while index < len(token):
            if token[index] != "~":
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                raise InputBindingError(f"非法 JSON Pointer 转义: {pointer}")
            index += 2


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """按 RFC 6901 解析 JSON Pointer。"""
    validate_json_pointer(pointer)
    if pointer == "":
        return document

    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(token, _MISSING)
        elif isinstance(current, list):
            if token == "-" or not token.isdigit():
                current = _MISSING
            else:
                index = int(token)
                current = current[index] if index < len(current) else _MISSING
        else:
            current = _MISSING
        if current is _MISSING:
            raise InputBindingError(f"结果路径不存在: {pointer}")
    return current


class ResultResolver:
    """把 PlanStep.input_bindings 解析为本次执行参数。"""

    def resolve_args(self, run: WorkflowRun, step: PlanStep) -> dict[str, Any]:
        resolved = deepcopy(step.args)
        for argument, binding in step.input_bindings.items():
            try:
                value = resolve_result_reference(run, binding, require_source_success=True)
            except InputBindingError:
                if binding.required:
                    raise
                continue
            resolved[argument] = deepcopy(value)
        logger.info("解析本次执行参数",resolved=resolved)
        return resolved


def resolve_result_reference(
    run: WorkflowRun,
    reference: ResultRef,
    *,
    require_source_success: bool,
) -> Any:
    """解析一个稳定结果引用。"""
    source = run.step(reference.source_step_id)
    if require_source_success and source.status != StepStatus.SUCCEEDED:
        raise InputBindingError(f"来源步骤未成功: {reference.source_step_id}")
    if source.result is None:
        raise InputBindingError(f"来源步骤无结果: {reference.source_step_id}")
    return resolve_json_pointer(source.result.model_dump(mode="json"), reference.pointer)
