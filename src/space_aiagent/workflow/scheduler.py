"""确定性 Scheduler、前置条件和结束守卫。"""

from dataclasses import dataclass
from typing import Literal

from .models import (
    RunResult,
    RunStatus,
    StepError,
    StepStatus,
    WaitingContext,
    WorkflowRun,
    utc_now,
)


@dataclass(frozen=True)
class ScheduleDecision:
    outcome: Literal["execute", "wait", "finalize"]
    step_id: str | None = None


class PreconditionEngine:
    @staticmethod
    def facts(run: WorkflowRun) -> set[str]:
        facts: set[str] = set()
        if run.scene_context.status == "opened":
            facts.add("scene.opened")
        elif run.scene_context.status == "none":
            facts.add("scene.none")
        for step in run.steps:
            if step.status == StepStatus.SUCCEEDED:
                facts.update(step.provides)
                if step.result:
                    facts.update(step.result.effects)
        return facts


class Scheduler:
    def __init__(self, preconditions: PreconditionEngine | None = None) -> None:
        self._preconditions = preconditions or PreconditionEngine()

    def decide(self, run: WorkflowRun) -> ScheduleDecision:
        step_map = {step.step_id: step for step in run.steps}

        for step in run.steps:
            if step.status not in {StepStatus.PENDING, StepStatus.READY}:
                continue
            dependencies = [step_map[item] for item in step.depends_on]
            failed_dependencies = [
                item
                for item in dependencies
                if item.status in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.CANCELLED}
            ]
            if failed_dependencies:
                step.status = StepStatus.BLOCKED
                step.error = StepError(
                    code="DEPENDENCY_FAILED",
                    message="依赖步骤未成功：" + ", ".join(item.step_id for item in failed_dependencies),
                )
                step.updated_at = utc_now()

        active = [step for step in run.steps if step.status in {StepStatus.RUNNING, StepStatus.WAITING_TOOL}]
        if active:
            return ScheduleDecision("wait", active[0].step_id)

        facts = self._preconditions.facts(run)
        for step in run.steps:
            if step.status not in {StepStatus.PENDING, StepStatus.READY}:
                continue
            if any(step_map[item].status not in {StepStatus.SUCCEEDED, StepStatus.SKIPPED} for item in step.depends_on):
                continue
            if step.missing_arguments:
                step.status = StepStatus.WAITING_USER
                run.status = RunStatus.WAITING_USER
                run.waiting_context = WaitingContext(
                    kind="missing_arguments",
                    step_id=step.step_id,
                    prompt="请补充以下必需参数：" + "、".join(step.missing_arguments),
                    data={"missing_arguments": step.missing_arguments},
                )
                step.updated_at = utc_now()
                return ScheduleDecision("wait", step.step_id)
            if step.action == "ensure_scene_context":
                if "scene.opened" in facts:
                    step.status = StepStatus.SUCCEEDED
                    step.updated_at = utc_now()
                    continue
                step.status = StepStatus.WAITING_USER
                run.status = RunStatus.WAITING_USER
                run.waiting_context = WaitingContext(
                    kind="missing_precondition",
                    step_id=step.step_id,
                    prompt="当前没有打开场景，请选择打开已有场景或新建场景。",
                    data={"required_fact": "scene.opened", "choices": ["open_scene", "create_scene"]},
                )
                step.updated_at = utc_now()
                return ScheduleDecision("wait", step.step_id)
            missing_facts = [fact for fact in step.requires if fact not in facts]
            if missing_facts:
                step.status = StepStatus.BLOCKED
                step.error = StepError(
                    code="PRECONDITION_UNSATISFIED",
                    message="未满足前置条件：" + ", ".join(missing_facts),
                )
                step.updated_at = utc_now()
                continue
            step.status = StepStatus.READY
            run.status = RunStatus.RUNNING
            run.waiting_context = None
            return ScheduleDecision("execute", step.step_id)

        if any(step.status == StepStatus.WAITING_USER for step in run.steps):
            run.status = RunStatus.WAITING_USER
            return ScheduleDecision("wait")
        return ScheduleDecision("finalize")


class FinalizationGuard:
    def finalize(self, run: WorkflowRun) -> WorkflowRun:
        nonterminal = [
            step
            for step in run.steps
            if step.status
            in {
                StepStatus.PENDING,
                StepStatus.READY,
                StepStatus.RUNNING,
                StepStatus.WAITING_TOOL,
                StepStatus.WAITING_USER,
            }
        ]
        if nonterminal:
            raise RuntimeError("仍有未终结步骤，禁止结束 Run")

        failures = [
            step
            for step in run.steps
            if step.required and step.status in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.CANCELLED}
        ]
        successes = [step for step in run.steps if step.status == StepStatus.SUCCEEDED]
        if failures and successes:
            status = RunStatus.PARTIALLY_SUCCEEDED
            summary = "部分步骤已完成，但存在失败或阻塞的必需步骤。"
        elif failures:
            status = RunStatus.FAILED
            summary = "任务未完成。"
        else:
            status = RunStatus.SUCCEEDED
            summary = "任务已完成。"

        run.status = status
        run.waiting_context = None
        run.final_result = RunResult(
            status=status,
            summary=summary,
            steps=[
                {
                    "step_id": step.step_id,
                    "title": step.title,
                    "status": step.status.value,
                    "code": step.result.code if step.result else (step.error.code if step.error else ""),
                    "summary": step.result.summary if step.result else (step.error.message if step.error else ""),
                }
                for step in run.steps
            ],
            effects=[
                effect for step in successes for effect in (step.result.effects if step.result else step.provides)
            ],
            failures=[
                {
                    "step_id": step.step_id,
                    "code": step.error.code if step.error else "FAILED",
                    "message": step.error.message if step.error else "步骤失败",
                }
                for step in failures
            ],
        )
        return run
