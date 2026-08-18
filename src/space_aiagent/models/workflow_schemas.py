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
    # 已经发出工具请求，等待 前端 回告
    WAITING_TOOL = "waiting_tool"
    # 需要用户回答
    WAITING_USER = "waiting_user"
    SUCCEEDED = "succeeded"
    # 执行失败
    FAILED = "failed"
    # 因依赖步骤失败而无法执行。它不是自身执行失败，而是根本没有执行机会。
    BLOCKED = "blocked"
    # 根据工作流规则主动跳过，通常用于非必需步骤或已经不需要执行的分支。
    SKIPPED = "skipped"
    # 因整个 Run 被取消而终止。尚未完成的步骤通常都会转成这个状态。
    CANCELLED = "cancelled"


TERMINAL_STEP_STATUSES = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)


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


class DraftResultRef(BaseModel):
    """Planner 使用的本计划局部结果引用。

    ``pointer`` 的作用
    -----------------
    ``pointer`` 是一个 JSON Pointer（RFC 6901），告诉执行引擎"从源步骤的
    输出结果中**取哪个字段**的值"。

    默认 ``"/data"`` 表示取整个 ``result.data``。

    业务场景举例
    ------------
    用户说："搜索高分系列卫星，然后加载第一个卫星的轨道"

    step_1（搜索卫星）返回结果::

        {
          "status": "success",
          "data": [
            {"id": "GF-1", "name": "高分一号", "orbit_type": "LEO"},
            {"id": "GF-2", "name": "高分二号", "orbit_type": "SSO"}
          ]
        }

    step_2（加载轨道）需要第一个卫星的 id：::

        {
          "ref": "step_2",
          "action": "load_satellite_orbit",
          "input_bindings": {
            "satellite_id": {
              "source_ref": "step_1",
              "pointer": "/data/0/id",    // ← 从 step_1 结果中取 data[0].id → "GF-1"
              "required": true
            }
          }
        }

    ``pointer`` 取值示例
    -------------------
    ================= ====================================
    pointer            含义
    ================= ====================================
    ``"/data"``        取整个 data 字段（默认值）
    ``"/data/0/id"``   取 data 数组第一个元素的 id
    ``"/data/name"``   取 data 对象的 name 字段
    ``"/data/0"``      取 data 数组的第一个元素（整个对象）
    ================= ====================================
    """

    source_ref: str
    pointer: str = "/data"
    required: bool = True
    """该数据绑定是否必需。

    - ``True``（默认）：当前步骤**必须**拿到这个数据才能执行。如果源步骤失败
      或数据缺失，当前步骤会被阻塞（BLOCKED）。
    - ``False``：该数据是"尽力而为"的可选增强。如果源步骤失败或数据缺失，
      当前步骤仍然可以执行（参数使用默认值或留空）。

    业务场景举例
    ------------
    **required=True**：加载卫星轨道，必须要有 ``satellite_id``，否则无法执行::

        {
          "satellite_id": {
            "source_ref": "step_1",
            "pointer": "/data/0/id",
            "required": true   // ← 没有卫星 ID 就阻塞
          }
        }

    **required=False**：分析覆盖范围，可选传入时间范围。如果前序步骤没提供，
    就用默认值（当前时间）::

        {
          "time_range": {
            "source_ref": "step_1",
            "pointer": "/data/time_range",
            "required": false  // ← 没有时间范围也能执行，用默认值
          }
        }

    校验规则：``required=True`` 的 binding 不能引用 ``required=False`` 的步骤
    —— 因为非必需步骤可能被跳过，无法保证数据可用。
    """


class ResultRef(BaseModel):
    """PlanValidator 解析后的可信步骤结果引用。"""

    source_step_id: str
    pointer: str = "/data"
    required: bool = True


class DraftStep(BaseModel):
    """Planner (AI) 可输出的非可信步骤。

    ``ref`` vs ``depends_on`` 的区别
    -------------------------------
    - ``ref``：本步骤的**身份证号**，在本计划内唯一标识自己，供其他步骤引用。
    - ``depends_on``：本步骤**引用了哪些其他步骤的身份证号**，声明执行顺序依赖。

    业务场景举例
    ------------
    用户说："加载高分一号的轨道，并分析它的覆盖范围"

    Planner 可能生成两个步骤：::

        [
          {
            "ref": "step_1",
            "action": "load_satellite_orbit",
            "title": "加载高分一号轨道",
            "args": {"satellite_name": "高分一号"}
          },
          {
            "ref": "step_2",
            "action": "analyze_coverage",
            "title": "分析覆盖范围",
            "depends_on": ["step_1"]   // ← 引用 step_1 的 ref，表示"step_1 跑完我才能跑"
          }
        ]

    这里 ``step_1`` 的 ``ref`` 是 ``"step_1"``，它是自己的标识；
    ``step_2`` 的 ``depends_on`` 是 ``["step_1"]``，它引用了别人的标识来声明依赖。
    """

    ref: str = Field(description="本计划内唯一的短引用，例如 step_1（身份证号，供 depends_on / input_bindings 引用）")
    action: str = Field(description="ActionCatalog 中的 action 名")
    title: str = Field(description="给用户展示的简短步骤标题")
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, description="依赖步骤的 ref（执行顺序约束，不涉及数据传递）")
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

# 例1：用户要求“创建一个新场景”：
# StepResult(
#     status="success",
#     code="SCENE_CREATED",
#     summary="场景创建成功",
#     data={"scene_id": "scene-001"},
#     effects=["scene.opened"],
#     evidence={
#         "agent_status": "success",
#         "tool_call_count": 1,
#         "tool_result": {
#             "sceneId": "scene-001",
#             "sceneName": "测试场景",
#         },
#     },
# )
class StepResult(BaseModel):
    """
    单个步骤的执行结果。
    """

    # status: 执行状态，取值 "success" | "failed" | "waiting_user"
    #     - "success"      步骤执行成功，data 中包含业务数据
    #     - "failed"       步骤执行失败，error 字段包含错误详情
    #     - "waiting_user" 等待用户确认/输入，前端应展示交互 UI
    status: Literal["success", "failed", "waiting_user"]
    code: str
    summary: str
    data: list[dict[str, Any]] | dict[str, Any] | None = None
    #    artifacts: 步骤产生的产物引用列表，例如：
    # StepResult(
    #     status="success",
    #     code="ANALYSIS_COMPLETED",
    #     summary="可见性分析完成",
    #     data={"window_count": 12},
    #     artifacts=[
    #         ArtifactRef(
    #             artifact_id="report-1",
    #             kind="report",
    #             name="可见性分析报告",
    #             uri="/artifacts/report-1",
    #             media_type="application/pdf",
    #             metadata={},
    #         )
    #     ],
    # )
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    # effects: 步骤产生的副作用描述列表
    effects: list[str] = Field(default_factory=list)
    # evidence: 步骤执行的证据/元信息字典，承载执行器内部状态，不直接面向最终用户。
    evidence: dict[str, Any] = Field(default_factory=dict)
    # retryable: 是否可重试，失败时标记前端是否展示"重试"按钮
    retryable: bool = False
    error: StepError | None = None


class PlanStep(BaseModel):
    step_id: str
    action: str
    title: str
    args: dict[str, Any] = Field(default_factory=dict)
    # 执行顺序依赖。表示"步骤 B 必须在步骤 A 之后执行"，但不关心步骤 A 的输出数据。只是因为业务逻辑上 A 必须先完成。
    # 例子：用户说"在当前场景中加载高分一号的轨道"
    # {
    #     "ref": "step_1",
    #     "action": "ensure_scene_context",
    #     "title": "确认要使用的场景",
    #     "args": {}
    # }
    # {
    #     "ref": "step_2",
    #     "action": "load_satellite_orbit",
    #     "title": "加载高分一号轨道",
    #     "args": {"satellite_name": "高分一号"},
    #     "depends_on": ["step_1"]
    # }
    depends_on: list[str] = Field(default_factory=list)

    # 数据传递依赖。表示"步骤 B 的参数 X，需要从步骤 A 的输出结果中提取某个字段"。既隐含了执行顺序依赖，又指定了数据来源。
    # 例子：用户说"搜索高分系列卫星，然后加载第一个的轨道"
    # {
    #   "ref": "step_1",
    #   "action": "search_satellites",
    #   "title": "搜索高分系列卫星",
    #   "args": { "keyword": "高分" }
    # }
    # {
    #     "ref": "step_2",
    #     "action": "load_satellite_orbit",
    #     "title": "加载卫星轨道",
    #     "args": {},
    #     "depends_on": ["step_1"],
    #     "input_bindings": {
    #         "satellite_id": {
    #             "source_ref": "step_1",
    #             "pointer": "/data/0/id",
    #             "required": true
    #         }
    #     }
    # }
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

    kind: Literal["missing_precondition", "missing_arguments", "scene_selection", "approval", "agent_interrupt"]
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
