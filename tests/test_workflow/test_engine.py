import pytest

from space_aiagent.workflow.catalog import ActionCatalog
from space_aiagent.workflow.engine import WorkflowEngine
from space_aiagent.workflow.models import (
    DraftResultRef,
    DraftStep,
    PlanDraft,
    RunStatus,
    SceneContext,
    StepResult,
    StepStatus,
)
from space_aiagent.workflow.repository import SqliteRunRepository


class FakePlanner:
    def __init__(self, draft: PlanDraft) -> None:
        self.draft = draft

    async def plan(self, intent, scene_context):
        return self.draft

    async def resolve_waiting(self, user_input, waiting):
        raise AssertionError("测试使用显式 resume decision")


class RecordingExecutor:
    def __init__(self, *, fail_action: str | None = None) -> None:
        self.actions: list[str] = []
        self.fail_action = fail_action

    async def execute(self, run, step, execution_id):
        self.actions.append(step.action)
        if step.action == self.fail_action:
            return StepResult(status="failed", code="FAILED", summary="模拟失败")
        evidence = {"scene_name": step.args.get("scene_name", "新建场景")} if "scene.opened" in step.provides else {}
        return StepResult(
            status="success",
            code="OK",
            summary=f"{step.action} 完成",
            effects=step.provides,
            evidence=evidence,
        )


class SceneSelectionExecutor(RecordingExecutor):
    async def execute(self, run, step, execution_id):
        if step.action == "open_scene" and not step.args.get("scene_name"):
            self.actions.append(step.action)
            candidates = [{"scene_name": "火箭场景A"}, {"scene_name": "火箭场景B"}]
            return StepResult(
                status="waiting_user",
                code="SCENE_QUERIED",
                summary="找到多个场景，请选择。",
                data=candidates,
                evidence={"waiting_kind": "scene_selection", "candidates": candidates},
            )
        return await super().execute(run, step, execution_id)


class BindingExecutor(RecordingExecutor):
    def __init__(self, *, missing_source_value: bool = False) -> None:
        super().__init__()
        self.received_args: list[dict] = []
        self.missing_source_value = missing_source_value

    async def execute(self, run, step, execution_id):
        self.actions.append(step.action)
        self.received_args.append(step.args)
        if len(self.actions) == 1:
            data = {"other": "value"} if self.missing_source_value else {"scene_name": "火箭场景"}
            return StepResult(status="success", code="SCENE_QUERIED", summary="查询完成", data=data)
        return StepResult(status="success", code="SCENE_QUERIED", summary="复用完成")


async def _engine(tmp_path, draft, executor):
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    catalog = ActionCatalog.from_yaml()
    engine = WorkflowEngine(repository, catalog, FakePlanner(draft), executor, checkpointer=None)
    return engine, repository


async def test_engine_executes_compound_steps_in_deterministic_order(tmp_path) -> None:
    draft = PlanDraft(
        goal="打开再添加",
        steps=[
            DraftStep(ref="open", action="open_scene", title="打开", args={"scene_name": "火箭场景"}),
            DraftStep(ref="add", action="add_entity", title="添加", depends_on=["open"]),
        ],
    )
    executor = RecordingExecutor()
    engine, _ = await _engine(tmp_path, draft, executor)
    run = await engine.create_run(
        thread_id="thread_1",
        intent="打开火箭场景再添加文昌地面站",
        scene_context=SceneContext(status="none"),
    )

    assert executor.actions == ["open_scene", "add_entity"]
    assert run.status == RunStatus.SUCCEEDED
    assert all(step.status == StepStatus.SUCCEEDED for step in run.steps)


async def test_engine_resumes_original_intent_after_scene_creation(tmp_path) -> None:
    draft = PlanDraft(goal="添加", steps=[DraftStep(ref="add", action="add_entity", title="添加文昌地面站")])
    executor = RecordingExecutor()
    engine, _ = await _engine(tmp_path, draft, executor)
    waiting = await engine.create_run(
        thread_id="thread_1",
        intent="添加文昌地面站",
        scene_context=SceneContext(status="none"),
    )
    assert waiting.status == RunStatus.WAITING_USER
    assert executor.actions == []

    completed = await engine.resume_run(
        waiting.run_id,
        user_input="新建场景",
        data={"decision": "create_scene", "args": {"scene_name": "自动场景"}},
    )
    assert executor.actions == ["create_scene", "add_entity"]
    assert completed.status == RunStatus.SUCCEEDED


async def test_engine_blocks_dependent_after_required_failure(tmp_path) -> None:
    draft = PlanDraft(
        goal="打开再添加",
        steps=[
            DraftStep(ref="open", action="open_scene", title="打开"),
            DraftStep(ref="add", action="add_entity", title="添加", depends_on=["open"]),
        ],
    )
    executor = RecordingExecutor(fail_action="open_scene")
    engine, _ = await _engine(tmp_path, draft, executor)
    run = await engine.create_run(
        thread_id="thread_1",
        intent="复合失败",
        scene_context=SceneContext(status="none"),
    )
    assert executor.actions == ["open_scene"]
    assert run.status == RunStatus.FAILED
    assert run.steps[1].status == StepStatus.BLOCKED


async def test_engine_resumes_scene_selection_then_continues_entity(tmp_path) -> None:
    draft = PlanDraft(
        goal="选择火箭场景后添加实体",
        steps=[
            DraftStep(ref="open", action="open_scene", title="打开火箭场景"),
            DraftStep(ref="add", action="add_entity", title="添加文昌", depends_on=["open"]),
        ],
    )
    executor = SceneSelectionExecutor()
    engine, _ = await _engine(tmp_path, draft, executor)
    waiting = await engine.create_run(
        thread_id="thread_select",
        intent="打开火箭场景再添加文昌地面站",
        scene_context=SceneContext(status="none"),
    )
    assert waiting.status == RunStatus.WAITING_USER
    assert waiting.waiting_context is not None
    assert waiting.waiting_context.kind == "scene_selection"

    completed = await engine.resume_run(
        waiting.run_id,
        user_input="火箭场景B",
        data={"scene_name": "火箭场景B"},
    )
    assert executor.actions == ["open_scene", "open_scene", "add_entity"]
    assert completed.status == RunStatus.SUCCEEDED
    assert completed.scene_context.scene_name == "火箭场景B"


async def test_engine_rejects_scene_selection_outside_candidates(tmp_path) -> None:
    draft = PlanDraft(goal="选择场景", steps=[DraftStep(ref="open", action="open_scene", title="打开")])
    executor = SceneSelectionExecutor()
    engine, _ = await _engine(tmp_path, draft, executor)
    waiting = await engine.create_run(
        thread_id="thread_invalid_select",
        intent="打开火箭场景",
        scene_context=SceneContext(status="none"),
    )

    with pytest.raises(ValueError, match="不在候选列表"):
        await engine.resume_run(
            waiting.run_id,
            user_input="火箭场景C",
            data={"scene_name": "火箭场景C"},
        )


async def test_engine_resolves_explicit_step_result_binding(tmp_path) -> None:
    draft = PlanDraft(
        goal="查询后复用结果",
        steps=[
            DraftStep(ref="source", action="query_scene", title="查询"),
            DraftStep(
                ref="consumer",
                action="query_scene",
                title="按真实名称查询",
                input_bindings={"scene_name": DraftResultRef(source_ref="source", pointer="/data/scene_name")},
            ),
        ],
    )
    executor = BindingExecutor()
    engine, _ = await _engine(tmp_path, draft, executor)

    run = await engine.create_run(
        thread_id="thread_binding",
        intent="查询后复用",
        scene_context=SceneContext(status="none"),
    )

    assert run.status == RunStatus.SUCCEEDED
    assert executor.received_args == [{}, {"scene_name": "火箭场景"}]
    assert run.steps[1].args == {}


async def test_engine_marks_missing_required_binding_as_non_retryable_failure(tmp_path) -> None:
    draft = PlanDraft(
        goal="缺失结果",
        steps=[
            DraftStep(ref="source", action="query_scene", title="查询"),
            DraftStep(
                ref="consumer",
                action="query_scene",
                title="消费",
                input_bindings={"scene_name": DraftResultRef(source_ref="source", pointer="/data/scene_name")},
            ),
        ],
    )
    executor = BindingExecutor(missing_source_value=True)
    engine, _ = await _engine(tmp_path, draft, executor)

    run = await engine.create_run(
        thread_id="thread_missing_binding",
        intent="缺失结果",
        scene_context=SceneContext(status="none"),
    )

    assert executor.actions == ["query_scene"]
    assert run.status == RunStatus.PARTIALLY_SUCCEEDED
    assert run.steps[1].status == StepStatus.FAILED
    assert run.steps[1].result is not None
    assert run.steps[1].result.code == "INPUT_BINDING_ERROR"
