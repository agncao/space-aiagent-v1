import pytest

from space_aiagent.workflow.models import (
    ArtifactRef,
    PlanStep,
    ResultRef,
    StepResult,
    StepStatus,
    WorkflowRun,
)
from space_aiagent.workflow.result_resolver import (
    InputBindingError,
    ResultResolver,
    resolve_json_pointer,
)


def _step(step_id: str, *, result: StepResult | None = None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action="query_scene",
        title=step_id,
        executor="scene-agent",
        status=StepStatus.SUCCEEDED if result else StepStatus.PENDING,
        result=result,
    )


def test_json_pointer_handles_dict_list_and_escaping() -> None:
    document = {"data": [{"scene/name": "场景A", "a~b": 1}]}

    assert resolve_json_pointer(document, "/data/0/scene~1name") == "场景A"
    assert resolve_json_pointer(document, "/data/0/a~0b") == 1
    assert resolve_json_pointer(document, "") == document
    with pytest.raises(InputBindingError, match="非法 JSON Pointer 转义"):
        resolve_json_pointer(document, "/data/~2")


def test_result_resolver_resolves_data_and_artifact_without_mutating_plan() -> None:
    source = _step(
        "source",
        result=StepResult(
            status="success",
            code="OK",
            summary="ok",
            data={"entity": {"id": "facility-1"}},
            artifacts=[
                ArtifactRef(
                    artifact_id="report-1",
                    kind="report",
                    name="可见性报告",
                    uri="/artifacts/report-1",
                )
            ],
        ),
    )
    target = _step("target")
    target.args = {"fixed": True}
    target.input_bindings = {
        "facility_id": ResultRef(source_step_id="source", pointer="/data/entity/id"),
        "report_uri": ResultRef(source_step_id="source", pointer="/artifacts/0/uri"),
    }
    run = WorkflowRun(run_id="run", thread_id="thread", original_intent="test", steps=[source, target])

    resolved = ResultResolver().resolve_args(run, target)

    assert resolved == {"fixed": True, "facility_id": "facility-1", "report_uri": "/artifacts/report-1"}
    assert target.args == {"fixed": True}


def test_result_resolver_distinguishes_required_and_optional_missing_paths() -> None:
    source = _step(
        "source",
        result=StepResult(status="success", code="OK", summary="ok", data={"entity_id": "facility-1"}),
    )
    target = _step("target")
    run = WorkflowRun(run_id="run", thread_id="thread", original_intent="test", steps=[source, target])

    target.input_bindings = {"optional": ResultRef(source_step_id="source", pointer="/data/missing", required=False)}
    assert ResultResolver().resolve_args(run, target) == {}

    target.input_bindings = {"required": ResultRef(source_step_id="source", pointer="/data/missing")}
    with pytest.raises(InputBindingError, match="结果路径不存在"):
        ResultResolver().resolve_args(run, target)
