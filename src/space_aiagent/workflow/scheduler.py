"""确定性 Scheduler、前置条件和结束守卫。"""

from dataclasses import dataclass
from typing import Literal

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.models.workflow_schemas import (
    RunResult,
    RunStatus,
    StepError,
    StepStatus,
    WaitingContext,
    WorkflowRun,
    utc_now,
)


logger = get_logger(__name__)

@dataclass(frozen=True)
class ScheduleDecision:
    outcome: Literal["execute", "wait", "finalize"]
    step_id: str | None = None


class PreconditionEngine:
    @staticmethod
    def facts(run: WorkflowRun) -> set[str]:
        """
        收集当前 Run 已成立的前置事实（facts）:
        1. 场景上下文注入 scene.opened |scene.none事实
        2. 已成功步骤提供的 scene.opened |scene.candidates  |scene.none事实
        3. 已成功步骤的动态产出 effects
        """
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
        """
        1. 所有steps 都在 正在运行/已经运行过了/BLOCKED 则 返回 finalize
        2. 有一步骤 缺参数了，返回 wait
        3. 发现有一步骤 缺打开的场景了，返回 wait

        4. 以上都不是，至少有一步骤是PENDING/READY 则返回 execute
        """
        step_map = {step.step_id: step for step in run.steps}

        # 第一遍：只要某步骤依赖了失败/阻塞/取消的步骤，本步骤标记为 BLOCKED，
        for step in run.steps:
            # 只处理尚未开始的步骤，已进入运行/终结态的不再重复处理
            if step.status not in {StepStatus.PENDING, StepStatus.READY}:
                continue
            dependencies = [step_map[item] for item in step.depends_on]
            # 找出处于失败/阻塞/取消等"未成功"终结态的依赖项
            failed_dependencies = [
                item
                for item in dependencies
                if item.status in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.CANCELLED}
            ]
            if failed_dependencies:
                # 标记该步骤为 BLOCKED
                step.status = StepStatus.BLOCKED
                step.error = StepError(
                    code="DEPENDENCY_FAILED",
                    message="依赖步骤未成功：" + ", ".join(item.step_id for item in failed_dependencies),
                )
                step.updated_at = utc_now()

        # 安全检查：若仍有正在执行或等待工具结果的步骤，本轮不做新调度，等待其完成
        active = [step for step in run.steps if step.status in {StepStatus.RUNNING, StepStatus.WAITING_TOOL}]
        if active:
            return ScheduleDecision("wait", active[0].step_id)

        # 计算当前 Run 已成立的前置事实集合（由场景状态与已成功步骤的 provides/effects 提供）
        facts = self._preconditions.facts(run)
        # 第二遍：在依赖均已成功的前提下，按顺序挑选下一个可执行的步骤
        for step in run.steps:
            # 只运行PENDING/READY, 正在运行/已经运行过了/BLOCKED 继续
            if step.status not in {StepStatus.PENDING, StepStatus.READY}:
                continue
            # 依赖尚未全部成功（或跳过）的步骤本轮跳过，等其依赖就绪
            if any(step_map[item].status not in {StepStatus.SUCCEEDED, StepStatus.SKIPPED} for item in step.depends_on):
                continue
            # 缺少必需参数：进入等待用户补充状态，由前端收集后再继续
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
                logger.info(f"规划器发现子智能体{step.executor}执行{step.action}时,缺失参数，"
                            f"准备跳转到 'wait' graph节点",
                            thread_id=run.thread_id,run_id=run.id,
                            args=step.args,kind=run.waiting_context.kind)
                return ScheduleDecision("wait", step.step_id)
            # 特殊动作：确保场景上下文存在。已打开则直接视为成功，否则转为等待用户选择打开/新建场景
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
                logger.info(f"规划器发现子智能体{step.executor}执行{step.action}时,未打开场景，"
                            f"准备跳转到 'wait' graph节点",
                            thread_id=run.thread_id,run_id=run.id,
                            args=step.args,kind=run.waiting_context.kind)
                step.updated_at = utc_now()
                return ScheduleDecision("wait", step.step_id)
            # 校验步骤要求的前置事实是否全部成立；缺失则阻塞并记录原因
            missing_facts = [fact for fact in step.requires if fact not in facts]
            if missing_facts:
                step.status = StepStatus.BLOCKED
                step.error = StepError(
                    code="PRECONDITION_UNSATISFIED",
                    message="未满足前置条件：" + ", ".join(missing_facts),
                )
                step.updated_at = utc_now()
                continue
            # 所有条件满足：置为 READY，并指示引擎立即执行该步骤
            step.status = StepStatus.READY
            run.status = RunStatus.RUNNING
            run.waiting_context = None
            return ScheduleDecision("execute", step.step_id)

        # 走到这里说明本轮没有可执行步骤：若任一步骤在等待用户，则整体维持等待态
        if any(step.status == StepStatus.WAITING_USER for step in run.steps):
            run.status = RunStatus.WAITING_USER
            logger.info(f"规划器发现子智能体{step.executor}还有等待用户的步骤，"
                        f"准备跳转到 'wait' graph节点",
                        thread_id=run.thread_id, run_id=run.id,
                        args=step.args, kind=run.waiting_context.kind)
            return ScheduleDecision("wait")
        # 无可执行步骤且无人等待用户输入：全部步骤已终结，可结束本次 Run
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
