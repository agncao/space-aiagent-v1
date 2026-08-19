"""Worker Todo 的确定性调度、失败传播和结束守卫。"""

from dataclasses import dataclass
from typing import Literal

from space_aiagent.models.workflow_schemas import (
    RunResult,
    RunStatus,
    StepError,
    StepStatus,
    WorkflowRun,
    utc_now,
)


@dataclass(frozen=True)
class ScheduleDecision:
    outcome: Literal["execute", "wait", "finalize"]
    step_id: str | None = None


class Scheduler:
    def decide(self, run: WorkflowRun) -> ScheduleDecision:
        step_map = {step.step_id: step for step in run.steps}

        for step in run.steps:
            if step.status not in {StepStatus.PENDING, StepStatus.READY, StepStatus.WAITING_DEPENDENCY}:
                continue
            dependencies = [step_map[item] for item in step.depends_on]
            failed = [
                item
                for item in dependencies
                if item.status in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.CANCELLED}
            ]
            if failed:
                step.status = StepStatus.BLOCKED
                step.error = StepError(
                    code="DEPENDENCY_FAILED",
                    message="依赖 Todo 未成功：" + ", ".join(item.step_id for item in failed),
                )
                step.updated_at = utc_now()

        active = [step for step in run.steps if step.status == StepStatus.RUNNING]
        if active:
            return ScheduleDecision("wait", active[0].step_id)

        for step in run.steps:
            if step.status == StepStatus.WAITING_DEPENDENCY:
                dependencies = [step_map[item] for item in step.depends_on]
                if dependencies and all(item.status == StepStatus.SUCCEEDED for item in dependencies):
                    step.status = StepStatus.PENDING
                    step.result = None
                    step.error = None
                    step.agent_thread_id = None
                    step.resume_payload = None
                    step.resume_user_input = None
                    step.updated_at = utc_now()
                else:
                    continue

            if step.status not in {StepStatus.PENDING, StepStatus.READY}:
                continue
            if any(step_map[item].status != StepStatus.SUCCEEDED for item in step.depends_on):
                continue
            step.status = StepStatus.READY
            run.status = RunStatus.RUNNING
            run.waiting_context = None
            return ScheduleDecision("execute", step.step_id)

        if any(step.status == StepStatus.WAITING_USER for step in run.steps):
            run.status = RunStatus.WAITING_USER
            return ScheduleDecision("wait")
        if any(step.status == StepStatus.WAITING_DEPENDENCY for step in run.steps):
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
                StepStatus.WAITING_USER,
                StepStatus.WAITING_DEPENDENCY,
            }
        ]
        if nonterminal:
            raise RuntimeError("仍有未终结 Todo，禁止结束 Run")

        failures = [
            step
            for step in run.steps
            if step.required and step.status in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.CANCELLED}
        ]
        successes = [step for step in run.steps if step.status == StepStatus.SUCCEEDED]
        if failures and successes:
            status = RunStatus.PARTIALLY_SUCCEEDED
        elif failures:
            status = RunStatus.FAILED
        else:
            status = RunStatus.SUCCEEDED

        summaries = [step.result.summary for step in successes if step.result and step.result.summary]
        failure_summaries = [
            step.error.message if step.error else (step.result.summary if step.result else step.task)
            for step in failures
        ]
        fallback_summary = "；".join([*summaries, *failure_summaries]) or "未产生可展示的执行结果。"

        run.status = status
        run.waiting_context = None
        run.final_result = RunResult(
            status=status,
            summary=fallback_summary,
            steps=[
                {
                    "step_id": step.step_id,
                    "worker": step.worker,
                    "task": step.task,
                    "source": step.source.value,
                    "status": step.status.value,
                    "code": step.result.code if step.result else (step.error.code if step.error else ""),
                    "summary": step.result.summary if step.result else (step.error.message if step.error else ""),
                }
                for step in run.steps
            ],
            effects=[effect for step in successes for effect in (step.result.effects if step.result else [])],
            failures=[
                {
                    "step_id": step.step_id,
                    "code": step.error.code if step.error else "FAILED",
                    "message": step.error.message if step.error else "Todo 执行失败",
                }
                for step in failures
            ],
        )
        return run
