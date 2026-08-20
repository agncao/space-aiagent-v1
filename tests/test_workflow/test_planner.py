from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from space_aiagent.models.workflow_schemas import (
    DraftStep,
    PlanDraft,
    SceneContext,
    WorkerRequirement,
    WorkerTodoSource,
    WorkflowRun,
)
from space_aiagent.workflow.catalog import WorkerCatalog
from space_aiagent.workflow.planner import FinalAnswer, StructuredPlanner


class RecordingStructuredRunnable:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] | None = None
        self.messages = None

    async def ainvoke(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return self.result


class RecordingModel:
    def __init__(self, plan: PlanDraft) -> None:
        self.plan = plan
        self.runnables: dict[type, RecordingStructuredRunnable] = {}

    def with_structured_output(self, schema):
        result = self.plan if schema is PlanDraft else FinalAnswer(content="当前场景共有 3 个实体。")
        runnable = RecordingStructuredRunnable(result)
        self.runnables[schema] = runnable
        return runnable


def _draft(source: WorkerTodoSource = WorkerTodoSource.USER_INTENT) -> PlanDraft:
    return PlanDraft(
        goal="打开场景",
        todos=[DraftStep(ref="todo_1", worker="scene-agent", task="打开火箭场景", source=source)],
    )


async def test_planner_only_exposes_workers_and_disables_streaming() -> None:
    model = RecordingModel(_draft())
    planner = StructuredPlanner(WorkerCatalog.from_yaml(), model=model)

    result = await planner.plan("打开火箭场景", SceneContext(status="none"))

    assert result.todos[0].worker == "scene-agent"
    assert result.todos[0].source == WorkerTodoSource.USER_INTENT
    assert set(result.todos[0].model_dump()) == {
        "ref",
        "worker",
        "task",
        "source",
        "depends_on",
        "required",
    }
    runnable = model.runnables[PlanDraft]
    assert runnable.kwargs == {"stream": False}
    assert "scene-agent" in runnable.messages[0].content
    assert "query_scenario" not in runnable.messages[0].content


async def test_planner_injects_conversation_history_into_human_prompt() -> None:
    model = RecordingModel(_draft())
    planner = StructuredPlanner(WorkerCatalog.from_yaml(), model=model)
    history = [
        "用户：查询包含火箭的场景",
        "助手：找到 4 个场景：1.场景0942_ 1个火箭_1个卫星关节动画 2.火箭测试",
    ]

    await planner.plan(
        "打开第二个，然后统计它的实体数量",
        SceneContext(status="none"),
        history=history,
    )

    runnable = model.runnables[PlanDraft]
    human = runnable.messages[1].content
    assert "查询包含火箭的场景" in human
    assert "打开第二个，然后统计它的实体数量" in human
    # 规则必须约束历史用途，防止 Planner 从历史生成 Todo
    assert "历史仅用于消解指代" in runnable.messages[0].content or "历史仅用于消解指代" in human


async def test_requirement_planner_marks_requirement_source() -> None:
    model = RecordingModel(_draft(WorkerTodoSource.REQUIREMENT))
    planner = StructuredPlanner(WorkerCatalog.from_yaml(), model=model)

    result = await planner.plan_requirement(
        WorkerRequirement(key="scene.opened", description="需要打开场景"),
        blocked_worker="entity-agent",
        blocked_task="统计实体数",
        scene_context=SceneContext(status="none"),
    )

    assert result.todos[0].source == WorkerTodoSource.REQUIREMENT
    assert (
        "entity-agent"
        not in model.runnables[PlanDraft].messages[0].content.split("可用 Worker（已排除当前 Worker）：", 1)[1]
    )


async def test_requirement_planner_rejects_fact_without_provider() -> None:
    model = RecordingModel(_draft(WorkerTodoSource.REQUIREMENT))
    planner = StructuredPlanner(WorkerCatalog.from_yaml(), model=model)

    with pytest.raises(ValueError, match="没有 Worker 能提供 requirement"):
        await planner.plan_requirement(
            WorkerRequirement(key="unknown.fact", description="未知事实"),
            blocked_worker="entity-agent",
            blocked_task="统计实体数",
            scene_context=SceneContext(status="none"),
        )


async def test_finalizer_uses_structured_output_without_streaming() -> None:
    model = RecordingModel(_draft())
    planner = StructuredPlanner(WorkerCatalog.from_yaml(), model=model)
    run = WorkflowRun(run_id="run_1", thread_id="thread_1", original_intent="统计实体数")

    content = await planner.finalize([HumanMessage(content="统计实体数")], run)

    assert content == "当前场景共有 3 个实体。"
    assert model.runnables[FinalAnswer].kwargs == {"stream": False}
