"""Worker Todo 草案的确定性校验与持久化模型装配。"""

import uuid

from space_aiagent.models.workflow_schemas import PlanDraft, PlanStep, WorkerTodoSource
from space_aiagent.workflow.catalog import WorkerCatalog


class PlanValidationError(ValueError):
    pass


class PlanValidator:
    def __init__(self, catalog: WorkerCatalog) -> None:
        self._catalog = catalog

    def validate(
        self,
        draft: PlanDraft,
        *,
        expected_source: WorkerTodoSource,
        generated_for_step_id: str | None = None,
        requirement_key: str | None = None,
        dependency_depth: int = 0,
        inherited_dependencies: list[str] | None = None,
    ) -> list[PlanStep]:
        if not draft.todos:
            raise PlanValidationError("Planner 未生成任何 Todo")

        refs = [todo.ref for todo in draft.todos]
        if len(set(refs)) != len(refs):
            raise PlanValidationError("PlanDraft 存在重复 ref")
        ref_index = {ref: index for index, ref in enumerate(refs)}

        for todo in draft.todos:
            if todo.source != expected_source:
                raise PlanValidationError(
                    f"Todo {todo.ref} 来源必须是 {expected_source.value}，实际为 {todo.source.value}"
                )
            if not self._catalog.contains(todo.worker):
                raise PlanValidationError(f"未知 Worker: {todo.worker}")
            for dependency in todo.depends_on:
                if dependency not in ref_index:
                    raise PlanValidationError(f"Todo {todo.ref} 依赖未知 ref: {dependency}")
                if ref_index[dependency] >= ref_index[todo.ref]:
                    raise PlanValidationError("depends_on 必须指向前序 Todo，禁止环和前向引用")

        step_ids = {ref: f"step_{uuid.uuid4().hex[:12]}" for ref in refs}
        inherited = list(inherited_dependencies or [])
        steps: list[PlanStep] = []
        for todo in draft.todos:
            dependencies = [step_ids[ref] for ref in todo.depends_on]
            if not todo.depends_on:
                dependencies = [*inherited, *dependencies]
            steps.append(
                PlanStep(
                    step_id=step_ids[todo.ref],
                    worker=todo.worker,
                    task=todo.task,
                    source=todo.source,
                    generated_for_step_id=generated_for_step_id,
                    requirement_key=requirement_key,
                    depends_on=list(dict.fromkeys(dependencies)),
                    required=todo.required,
                    dependency_depth=dependency_depth,
                )
            )
        return steps

    @staticmethod
    def terminal_step_ids(steps: list[PlanStep]) -> list[str]:
        depended_on = {dependency for step in steps for dependency in step.depends_on}
        return [step.step_id for step in steps if step.step_id not in depended_on]
