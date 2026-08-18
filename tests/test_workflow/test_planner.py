from typing import Any

from space_aiagent.models.workflow_schemas import PlanDraft, SceneContext, WaitingContext
from space_aiagent.workflow.catalog import ActionCatalog
from space_aiagent.workflow.planner import ResumeDecision, StructuredPlanner


class RecordingStructuredRunnable:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] | None = None

    async def ainvoke(self, messages, **kwargs):
        self.kwargs = kwargs
        return self.result


class RecordingModel:
    def __init__(self) -> None:
        self.runnables: dict[type, RecordingStructuredRunnable] = {}

    def with_structured_output(self, schema):
        result = PlanDraft(goal="打开场景", steps=[]) if schema is PlanDraft else ResumeDecision(decision="cancel")
        runnable = RecordingStructuredRunnable(result)
        self.runnables[schema] = runnable
        return runnable


async def test_planner_disables_streaming_for_structured_output() -> None:
    model = RecordingModel()
    planner = StructuredPlanner(ActionCatalog.from_yaml(), model=model)

    await planner.plan("打开场景", SceneContext(status="none"))

    assert model.runnables[PlanDraft].kwargs == {"stream": False}


async def test_resume_parser_disables_streaming_for_structured_output() -> None:
    model = RecordingModel()
    planner = StructuredPlanner(ActionCatalog.from_yaml(), model=model)
    waiting = WaitingContext(kind="missing_precondition", step_id="step_1", prompt="请选择")

    await planner.resolve_waiting("取消", waiting)

    assert model.runnables[ResumeDecision].kwargs == {"stream": False}
