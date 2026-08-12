"""PlanDraft 的确定性校验与领域声明装配。"""

import uuid

from .catalog import ActionCatalog
from .models import PlanDraft, PlanStep, ResultRef, SceneContext
from .result_resolver import InputBindingError, validate_json_pointer


class PlanValidationError(ValueError):
    pass


class PlanValidator:
    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog

    def validate(self, draft: PlanDraft, scene_context: SceneContext) -> list[PlanStep]:
        if not draft.steps:
            raise PlanValidationError("Planner 未生成任何步骤")
        refs = [step.ref for step in draft.steps]
        if len(set(refs)) != len(refs):
            raise PlanValidationError("PlanDraft 存在重复 ref")
        ref_index = {ref: idx for idx, ref in enumerate(refs)}
        draft_by_ref = {step.ref: step for step in draft.steps}
        for draft_step in draft.steps:
            if not self._catalog.contains(draft_step.action):
                raise PlanValidationError(f"未知 action: {draft_step.action}")
            for dependency in draft_step.depends_on:
                if dependency not in ref_index:
                    raise PlanValidationError(f"步骤 {draft_step.ref} 依赖未知 ref: {dependency}")
                if ref_index[dependency] >= ref_index[draft_step.ref]:
                    raise PlanValidationError("depends_on 必须指向前序步骤，禁止环和前向引用")
            for argument, binding in draft_step.input_bindings.items():
                if argument in draft_step.args:
                    raise PlanValidationError(
                        f"步骤 {draft_step.ref} 的参数 {argument} 同时存在于 args 和 input_bindings"
                    )
                if binding.source_ref not in ref_index:
                    raise PlanValidationError(f"步骤 {draft_step.ref} 引用未知 ref: {binding.source_ref}")
                if ref_index[binding.source_ref] >= ref_index[draft_step.ref]:
                    raise PlanValidationError("input_bindings 必须引用前序步骤")
                try:
                    validate_json_pointer(binding.pointer)
                except InputBindingError as exc:
                    raise PlanValidationError(str(exc)) from exc
                if binding.required and not draft_by_ref[binding.source_ref].required:
                    raise PlanValidationError("required binding 不能引用非必需步骤")

        step_ids = {ref: f"step_{idx + 1:02d}_{uuid.uuid4().hex[:8]}" for idx, ref in enumerate(refs)}
        steps: list[PlanStep] = []
        scene_producer_id: str | None = None

        seen_scene_producer = False
        needs_synthetic_scene_step = False
        for item in draft.steps:
            definition = self._catalog.get(item.action)
            if "scene.opened" in definition.requires and not seen_scene_producer:
                needs_synthetic_scene_step = True
                break
            if "scene.opened" in definition.provides:
                seen_scene_producer = True

        if scene_context.status != "opened" and needs_synthetic_scene_step:
            ensure = self._catalog.get("ensure_scene_context")
            scene_producer_id = f"step_00_{uuid.uuid4().hex[:8]}"
            steps.append(
                PlanStep(
                    step_id=scene_producer_id,
                    action=ensure.name,
                    title="确认要使用的场景",
                    executor=ensure.executor,
                    allowed_tools=ensure.allowed_tools,
                    requires=ensure.requires,
                    provides=ensure.provides,
                    side_effect=ensure.side_effect,
                )
            )

        for draft_step in draft.steps:
            action = self._catalog.get(draft_step.action)
            dependencies = [step_ids[ref] for ref in draft_step.depends_on]
            input_bindings = {
                argument: ResultRef(
                    source_step_id=step_ids[binding.source_ref],
                    pointer=binding.pointer,
                    required=binding.required,
                )
                for argument, binding in draft_step.input_bindings.items()
            }
            for binding in input_bindings.values():
                if binding.source_step_id not in dependencies:
                    dependencies.append(binding.source_step_id)

            earlier_scene_producers = [
                item.step_id
                for item in steps
                if "scene.opened" in item.provides and item.action != "ensure_scene_context"
            ]
            if "scene.opened" in action.requires and scene_context.status != "opened":
                producer = earlier_scene_producers[-1] if earlier_scene_producers else scene_producer_id
                if producer and not self._depends_on(dependencies, producer, steps):
                    dependencies.append(producer)

            plan_step = PlanStep(
                step_id=step_ids[draft_step.ref],
                action=action.name,
                title=draft_step.title,
                args=draft_step.args,
                depends_on=dependencies,
                input_bindings=input_bindings,
                requires=action.requires,
                provides=action.provides,
                required=draft_step.required,
                executor=action.executor,
                allowed_tools=action.allowed_tools,
                missing_arguments=draft_step.missing_arguments,
                side_effect=action.side_effect,
            )
            steps.append(plan_step)
            if "scene.opened" in action.provides:
                scene_producer_id = plan_step.step_id

        self._assert_acyclic(steps)
        return steps

    @staticmethod
    def _depends_on(dependencies: list[str], target: str, built_steps: list[PlanStep]) -> bool:
        """判断当前依赖集合是否已传递依赖 target，避免冗余边。"""
        graph = {step.step_id: step.depends_on for step in built_steps}
        pending = list(dependencies)
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(graph.get(current, []))
        return False

    @staticmethod
    def _assert_acyclic(steps: list[PlanStep]) -> None:
        graph = {step.step_id: set(step.depends_on) for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise PlanValidationError("计划依赖存在环")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                if dependency not in graph:
                    raise PlanValidationError(f"依赖步骤不存在: {dependency}")
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for step_id in graph:
            visit(step_id)
