"""PlanDraft 的确定性校验与领域声明装配。"""

import uuid

from space_aiagent.models.workflow_schemas import PlanDraft, PlanStep, ResultRef, SceneContext

from .catalog import ActionCatalog
from .result_resolver import InputBindingError, validate_json_pointer


class PlanValidationError(ValueError):
    pass


class PlanValidator:
    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog

    def validate(self, draft: PlanDraft, scene_context: SceneContext) -> list[PlanStep]:
        """将 Planner 产出的 PlanDraft 校验并装配为可执行的 PlanStep 列表。

        分为三个阶段：
        1. 结构校验 —— 检查草案的合法性
        2. 自动注入 —— 必要时插入 ensure_scene_context 步骤
        3. 步骤装配 —— 将 DraftStep 转为 PlanStep，补全依赖 & 元数据
        """
        # ============================================================
        # 阶段 1：结构校验 —— 逐项检查草案合法性
        # ============================================================

        # 1a. 计划不能为空
        if not draft.steps:
            raise PlanValidationError("Planner 未生成任何步骤")

        # 1b. 步骤 ref 不能重复
        refs = [step.ref for step in draft.steps]
        if len(set(refs)) != len(refs):
            raise PlanValidationError("PlanDraft 存在重复 ref")

        ref_index = {ref: idx for idx, ref in enumerate(refs)}  # ref → 位置索引
        draft_by_ref = {step.ref: step for step in draft.steps}  # ref → DraftStep

        for draft_step in draft.steps:
            # 1c. action 必须在 ActionCatalog 中注册
            if not self._catalog.contains(draft_step.action):
                raise PlanValidationError(f"未知 action: {draft_step.action}")

            for dependency in draft_step.depends_on:
                # 1d. 依赖的 ref 必须存在
                if dependency not in ref_index:
                    raise PlanValidationError(f"步骤 {draft_step.ref} 依赖未知 ref: {dependency}")
                # 1e. 依赖只能指向前面步骤，禁止环 & 前向引用
                if ref_index[dependency] >= ref_index[draft_step.ref]:
                    raise PlanValidationError("depends_on 必须指向前序步骤，禁止环和前向引用")

            for argument, binding in draft_step.input_bindings.items():
                # 1f. 参数不能同时出现在 args 和 input_bindings 中
                if argument in draft_step.args:
                    raise PlanValidationError(
                        f"步骤 {draft_step.ref} 的参数 {argument} 同时存在于 args 和 input_bindings"
                    )
                # 1g. input_bindings 的 source_ref 必须存在
                if binding.source_ref not in ref_index:
                    raise PlanValidationError(f"步骤 {draft_step.ref} 引用未知 ref: {binding.source_ref}")
                # 1h. input_bindings 只能引用前序步骤
                if ref_index[binding.source_ref] >= ref_index[draft_step.ref]:
                    raise PlanValidationError("input_bindings 必须引用前序步骤")
                # 1i. JSON Pointer 语法合法
                try:
                    validate_json_pointer(binding.pointer)
                except InputBindingError as exc:
                    raise PlanValidationError(str(exc)) from exc
                # 1j. required binding 不能引用非必需步骤（否则无法保证数据可用）
                if binding.required and not draft_by_ref[binding.source_ref].required:
                    raise PlanValidationError("required binding 不能引用非必需步骤")

        # ============================================================
        # 阶段 2：自动注入 —— 必要时插入 ensure_scene_context 步骤
        # ============================================================

        # 为每个步骤生成全局唯一 step_id
        step_ids = {ref: f"step_{idx + 1:02d}_{uuid.uuid4().hex[:8]}" for idx, ref in enumerate(refs)}
        steps: list[PlanStep] = []
        scene_producer_id: str | None = None

        # 2a. 扫描规划：
        # 如果步骤中依赖于打开场景，且之前没有已打开场景或者没有打开场景的动作，则：
        # 将是否需要生成打开场景计划(needs_synthetic_scene_step)置为True
        seen_scene_producer = False  # 还没看到"打开场景"的步骤
        needs_synthetic_scene_step = False  # 是否需要生成'打开场景'计划
        for item in draft.steps:
            definition = self._catalog.get(item.action)
            if "scene.opened" in definition.requires and not seen_scene_producer:
                needs_synthetic_scene_step = True  # ← 有步骤需要场景，但前面没有打开场景的步骤
                break
            if "scene.opened" in definition.provides:
                seen_scene_producer = True  # ← 此步骤能打开场景，所以标记已看到

        # 2b. 如果需要打开场景，则注入一个打开场景的计划
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

        # ============================================================
        # 阶段 3：步骤装配 —— DraftStep → PlanStep，补全依赖 & 元数据
        # ============================================================

        for draft_step in draft.steps:
            action = self._catalog.get(draft_step.action)

            # 3a. 将依赖的 ref 字符串转换为真实 step_id
            dependencies = [step_ids[ref] for ref in draft_step.depends_on]

            # 3b. 将 input_bindings 中的 DraftResultRef 转为 ResultRef（含真实 step_id）
            input_bindings = {
                argument: ResultRef(
                    source_step_id=step_ids[binding.source_ref],
                    pointer=binding.pointer,
                    required=binding.required,
                )
                for argument, binding in draft_step.input_bindings.items()
            }
            # 3c. input_bindings 的 source_step_id 也加入依赖列表
            for binding in input_bindings.values():
                if binding.source_step_id not in dependencies:
                    dependencies.append(binding.source_step_id)

            # 3d. 自动补全 scene.opened 的传递依赖
            #     如果当前步骤需要 scene.opened 但场景未打开，找到之前产出的场景步骤作为依赖
            earlier_scene_producers = [
                item.step_id
                for item in steps
                if "scene.opened" in item.provides and item.action != "ensure_scene_context"
            ]
            if "scene.opened" in action.requires and scene_context.status != "opened":
                producer = earlier_scene_producers[-1] if earlier_scene_producers else scene_producer_id
                if producer and not self._depends_on(dependencies, producer, steps):
                    dependencies.append(producer)

            # 3e. 组装 PlanStep，从 ActionCatalog 补全元数据
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

            # 3f. 记录 scene.opened 的生产者，供后续步骤自动补依赖
            if "scene.opened" in action.provides:
                scene_producer_id = plan_step.step_id

        # 3g. 最终环检测（DFS），确保完整依赖图无环
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
