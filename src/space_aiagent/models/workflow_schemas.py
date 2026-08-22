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
    # 待调度.步骤已经创建，但依赖步骤可能尚未完成。
    PENDING = "pending"
    # 可以执行。依赖和前置条件均已满足，等待执行器领取。
    READY = "ready"
    # 正在执行
    RUNNING = "running"
    # 需要用户回答
    WAITING_USER = "waiting_user"
    # 等待 Graph 动态插入的前置 Todo 完成，不直接等待用户输入
    WAITING_DEPENDENCY = "waiting_dependency"
    SUCCEEDED = "succeeded"
    # 执行失败
    FAILED = "failed"
    # 因依赖步骤失败而无法执行。它不是自身执行失败，而是根本没有执行机会。
    BLOCKED = "blocked"
    # 因整个 Run 被取消而终止。尚未完成的步骤通常都会转成这个状态。
    CANCELLED = "cancelled"


class SceneContext(BaseModel):
    status: Literal["unknown", "none", "opened"] = "unknown"
    scene_name: str | None = None
    revision: int = 0
    verified_at: datetime | None = None

    @classmethod
    def from_request(
        cls,
        *,
        scene_name: str | None,
        revision: int,
    ) -> SceneContext:
        status: Literal["unknown", "none", "opened"] = "opened" if scene_name else "none"
        return cls(
            status=status,
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


class ResultRef(BaseModel):
    """等待用户上下文引用的可信步骤结果。"""

    source_step_id: str
    pointer: str = "/data"


class WorkerTodoSource(StrEnum):
    USER_INTENT = "user_intent"
    REQUIREMENT = "requirement"


class DraftStep(BaseModel):
    """Planner 输出的非可信 Worker Todo 草案。"""

    ref: str = Field(description="本计划内唯一的短引用，供 depends_on 引用")
    worker: str = Field(description="负责完成 Todo 的 Worker 名称")
    task: str = Field(min_length=1, description="保留用户语义的自然语言任务")
    source: WorkerTodoSource
    depends_on: list[str] = Field(default_factory=list, description="依赖 Todo 的 ref")
    required: bool = True


class PlanDraft(BaseModel):
    goal: str
    todos: list[DraftStep]


class WorkerRequirement(BaseModel):
    """Worker 或工具执行期间发现的跨 Worker 前置要求。"""

    key: str = Field(min_length=1)
    description: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class StepError(BaseModel):
    code: str
    message: str


class StepResult(BaseModel):
    """单个 Worker Todo 的规范化业务结果。"""

    status: Literal["success", "failed", "waiting_user", "waiting_dependency"]
    code: str
    summary: str
    data: list[dict[str, Any]] | dict[str, Any] | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    invalidates: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    requirements: list[WorkerRequirement] = Field(default_factory=list)


class PlanStep(BaseModel):
    step_id: str
    worker: str
    task: str
    source: WorkerTodoSource
    generated_for_step_id: str | None = None
    requirement_key: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    required: bool = True
    status: StepStatus = StepStatus.PENDING
    result: StepResult | None = None
    error: StepError | None = None
    attempt_count: int = 0
    dependency_depth: int = 0
    dependency_expansion_keys: list[str] = Field(default_factory=list)
    agent_thread_id: str | None = None
    resume_payload: dict[str, Any] | None = None
    resume_user_input: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class WaitingContext(BaseModel):
    """工作流暂停并等待用户输入时持久化的上下文。

    Worker Todo 可通过 ``waiting_user`` 结果请求补充参数、选择候选项、审批
    或处理 Agent 中断。上下文随
    ``WorkflowRun`` 保存，并用于生成 SSE ``interrupt`` 事件，以及在用户
    提交恢复请求后帮助 Planner/Engine 将回答解析成结构化恢复决策。

    典型场景包括：补充任务信息、选择目标场景、确认有副作用的操作，以及继续
    Worker 发起的交互式中断。跨 Worker 前置条件不使用本对象。
    """

    kind: Literal["missing_arguments", "agent_interrupt","selection_required"]
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
    result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
