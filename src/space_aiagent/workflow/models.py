"""V2 工作流的协议模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回带时区的 UTC 当前时间。"""
    return datetime.now(UTC)


class RunStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.PARTIALLY_SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_USER = "waiting_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_STEP_STATUSES = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)


class SceneContext(BaseModel):
    status: Literal["unknown", "none", "opened"] = "unknown"
    scene_id: str | None = None
    scene_name: str | None = None
    revision: int = 0
    verified_at: datetime | None = None

    @classmethod
    def from_request(
        cls,
        *,
        scene_id: str | None,
        scene_name: str | None,
        revision: int,
    ) -> SceneContext:
        status: Literal["unknown", "none", "opened"] = "opened" if scene_name else "none"
        return cls(
            status=status,
            scene_id=scene_id,
            scene_name=scene_name,
            revision=revision,
            verified_at=utc_now(),
        )


class ArtifactRef(BaseModel):
    """大型分析产物的稳定引用；正文和二进制不进入 WorkflowRun。"""

    artifact_id: str
    kind: str
    name: str
    uri: str
    media_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftResultRef(BaseModel):
    """Planner 使用的本计划局部结果引用。"""

    source_ref: str
    pointer: str = "/data"
    required: bool = True


class ResultRef(BaseModel):
    """PlanValidator 解析后的可信步骤结果引用。"""

    source_step_id: str
    pointer: str = "/data"
    required: bool = True


class DraftStep(BaseModel):
    """Planner 可输出的非可信步骤。"""

    ref: str = Field(description="本计划内唯一的短引用，例如 step_1")
    action: str = Field(description="ActionCatalog 中的 action 名")
    title: str = Field(description="给用户展示的简短步骤标题")
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, description="依赖步骤的 ref")
    input_bindings: dict[str, DraftResultRef] = Field(default_factory=dict)
    required: bool = True
    missing_arguments: list[str] = Field(default_factory=list)


class PlanDraft(BaseModel):
    goal: str
    steps: list[DraftStep]


class StepError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None


class StepResult(BaseModel):
    status: Literal["success", "failed", "waiting_user"]
    code: str
    summary: str
    data: list[dict[str, Any]] | dict[str, Any] | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    error: StepError | None = None


class PlanStep(BaseModel):
    step_id: str
    action: str
    title: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    input_bindings: dict[str, ResultRef] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    required: bool = True
    executor: str
    allowed_tools: list[str] = Field(default_factory=list)
    missing_arguments: list[str] = Field(default_factory=list)
    side_effect: bool = False
    status: StepStatus = StepStatus.PENDING
    result: StepResult | None = None
    error: StepError | None = None
    attempt_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WaitingContext(BaseModel):
    """工作流暂停并等待用户输入时持久化的上下文。

    Scheduler 在缺少前置条件或必需参数时创建该对象；Worker 也可通过
    ``waiting_user`` 结果请求选场景、审批或处理 Agent 中断。上下文随
    ``WorkflowRun`` 保存，并用于生成 SSE ``interrupt`` 事件，以及在用户
    提交恢复请求后帮助 Planner/Engine 将回答解析成结构化恢复决策。

    典型场景包括：提示用户打开或新建场景、补充 action 参数、选择目标场景、
    确认有副作用的操作，以及继续 Worker 发起的交互式中断。
    """

    kind: Literal[
        "missing_precondition", "missing_arguments", "scene_selection", "approval", "agent_interrupt"
    ]
    """等待原因；决定恢复阶段应采用的交互语义和解析策略。"""

    step_id: str
    """触发暂停的步骤 ID，用于将用户回答准确应用回对应的 ``PlanStep``。"""

    prompt: str
    """展示给用户的提示语，说明当前缺少的信息或需要作出的决定。"""

    result_ref: ResultRef | None = None
    """可选的步骤结果引用；Worker 请求等待时指向其 ``StepResult.data``。"""

    data: dict[str, Any] = Field(default_factory=dict)
    """恢复所需的结构化补充信息，例如缺失参数、候选项、事实名或错误码。"""


class RunResult(BaseModel):
    status: RunStatus
    summary: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowRun(BaseModel):
    """一次工作流执行的完整生命周期记录。

    对应一次用户请求从规划、执行到最终完成（或失败/取消）的全过程。
    """

    run_id: str
    """单次执行的唯一标识，主键"""

    thread_id: str
    """所属会话（thread）的标识，一个 thread 下可以有多个 run"""

    original_intent: str
    """用户发起本次执行时的原始意图/问题文本"""

    status: RunStatus = RunStatus.PLANNING
    """当前执行状态，初始为 PLANNING"""

    revision: int = 0
    """修订次数：每次 replace 模式中断重试、或 resume 恢复时递增"""

    scene_context: SceneContext = Field(default_factory=SceneContext)
    """当前关联的 Cesium 场景信息（场景 ID、名称、状态等）"""

    steps: list[PlanStep] = Field(default_factory=list)
    """规划阶段生成的执行步骤列表，按序执行"""

    waiting_context: WaitingContext | None = None
    """当 status == WAITING_USER 时，记录等待用户输入的原因和上下文；非等待状态为 None"""

    final_result: RunResult | None = None
    """最终执行结果，仅在终端状态（SUCCEEDED / PARTIALLY_SUCCEEDED / FAILED / CANCELLED）时有值"""

    created_at: datetime = Field(default_factory=utc_now)
    """创建时间（UTC）"""

    updated_at: datetime = Field(default_factory=utc_now)
    """最后更新时间（UTC），每次状态变更时更新"""

    def step(self, step_id: str) -> PlanStep:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise KeyError(step_id)


class ToolExecution(BaseModel):
    execution_id: str
    run_id: str
    step_id: str
    tool_call_id: str
    idempotency_key: str
    fingerprint: str
    tool_func: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["started", "succeeded", "failed"] = "started"
    attempt: int = 1
    result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
