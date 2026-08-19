"""解析等待上下文中的步骤结果引用。

Worker Todo 不再通过 ResultRef 绑定执行参数；这里仅保留等待用户上下文投影
所需的只读解析能力。
"""

from __future__ import annotations

from typing import Any

from space_aiagent.models.workflow_schemas import ResultRef, StepStatus, WorkflowRun


class ResultReferenceError(ValueError):
    """结果引用无法解析。"""


_MISSING = object()


def validate_json_pointer(pointer: str) -> None:
    """校验 JSON Pointer 的基本 RFC 6901 语法。"""
    if pointer == "":
        return
    if not pointer.startswith("/"):
        raise ResultReferenceError(f"非法 JSON Pointer: {pointer}")
    for token in pointer[1:].split("/"):
        index = 0
        while index < len(token):
            if token[index] != "~":
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                raise ResultReferenceError(f"非法 JSON Pointer 转义: {pointer}")
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
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            current = current[index] if index < len(current) else _MISSING
        else:
            current = _MISSING
        if current is _MISSING:
            raise ResultReferenceError(f"结果路径不存在: {pointer}")
    return current


def resolve_result_reference(
    run: WorkflowRun,
    reference: ResultRef,
    *,
    require_source_success: bool,
) -> Any:
    """解析一个步骤的可信业务结果。"""
    source = run.step(reference.source_step_id)
    if require_source_success and source.status != StepStatus.SUCCEEDED:
        raise ResultReferenceError(f"来源步骤未成功: {reference.source_step_id}")
    if source.result is None:
        raise ResultReferenceError(f"来源步骤无结果: {reference.source_step_id}")
    return resolve_json_pointer(source.result.model_dump(mode="json"), reference.pointer)
