from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from space_aiagent.models.workflow_schemas import (
    DraftStep,
    PlanDraft,
    RunStatus,
    SceneContext,
    StepResult,
    StepStatus,
    WorkerRequirement,
    WorkerTodoSource,
)
from space_aiagent.workflow.catalog import WorkerCatalog, WorkerDefinition
from space_aiagent.workflow.engine import WorkflowEngine
from space_aiagent.workflow.repository import SqliteRunRepository


def _todo(
    ref: str,
    worker: str,
    task: str,
    *,
    source: WorkerTodoSource = WorkerTodoSource.USER_INTENT,
    depends_on: list[str] | None = None,
) -> DraftStep:
    return DraftStep(
        ref=ref,
        worker=worker,
        task=task,
        source=source,
        depends_on=depends_on or [],
    )


class FakePlanner:
    def __init__(self, draft: PlanDraft, requirement_draft: PlanDraft | None = None) -> None:
        self.draft = draft
        self.requirement_draft = requirement_draft
        self.final_messages = None
        self.requirements: list[WorkerRequirement] = []
        self.final_content = "最终回答"
        self.raise_on_finalize = False
        self.plan_history: list[str] | None = None

    async def plan(self, intent, scene_context, *, history=None):
        self.plan_history = history
        return self.draft

    async def plan_requirement(
        self,
        requirement,
        *,
        blocked_worker,
        blocked_task,
        scene_context,
    ):
        self.requirements.append(requirement)
        assert self.requirement_draft is not None
        return self.requirement_draft

    async def finalize(self, messages, run):
        self.final_messages = messages
        if self.raise_on_finalize:
            raise RuntimeError("finalizer unavailable")
        return self.final_content


class RecordingExecutor:
    def __init__(self, *, fail_task: str | None = None) -> None:
        self.tasks: list[str] = []
        self.fail_task = fail_task

    async def execute(self, run, step, execution_id):
        self.tasks.append(step.task)
        if step.task == self.fail_task:
            return StepResult(status="failed", code="FAILED", summary="模拟失败")
        return StepResult(status="success", code="OK", summary=f"{step.task}完成")


class RequirementExecutor:
    def __init__(self) -> None:
        self.tasks: list[str] = []
        self.entity_attempts = 0

    async def execute(self, run, step, execution_id):
        self.tasks.append(step.task)
        if step.worker == "entity-agent":
            self.entity_attempts += 1
            if self.entity_attempts == 1:
                return StepResult(
                    status="waiting_dependency",
                    code="REQUIREMENT_UNSATISFIED",
                    summary="需要先打开场景",
                    requirements=[
                        WorkerRequirement(
                            key="scene.opened",
                            description="请先创建场景或打开已有场景",
                        )
                    ],
                )
            return StepResult(
                status="success",
                code="ENTITIES_LIST",
                summary="当前场景共有 3 个实体",
                data={"count": 3},
            )
        return StepResult(
            status="success",
            code="SCENE_OPENED",
            summary="已打开火箭场景",
            effects=["scene.opened"],
            evidence={"scene_name": "火箭场景"},
        )


async def _engine(tmp_path, planner, executor, catalog=None):
    repository = SqliteRunRepository(tmp_path / "workflow.db")
    saver = InMemorySaver()
    engine = WorkflowEngine(
        repository,
        catalog or WorkerCatalog.from_yaml(),
        planner,
        executor,
        checkpointer=saver,
    )
    return engine, repository


async def test_engine_executes_worker_todos_in_dependency_order(tmp_path) -> None:
    draft = PlanDraft(
        goal="打开再添加",
        todos=[
            _todo("open", "scene-agent", "打开火箭场景"),
            _todo("add", "entity-agent", "添加文昌地面站", depends_on=["open"]),
        ],
    )
    planner = FakePlanner(draft)
    executor = RecordingExecutor()
    engine, _ = await _engine(tmp_path, planner, executor)

    run = await engine.create_run(
        thread_id="thread_1",
        intent="打开火箭场景再添加文昌地面站",
        scene_context=SceneContext(status="none"),
    )

    assert executor.tasks == ["打开火箭场景", "添加文昌地面站"]
    assert run.status == RunStatus.SUCCEEDED
    assert all(step.status == StepStatus.SUCCEEDED for step in run.steps)
    assert run.final_result is not None
    assert run.final_result.summary == "最终回答"


async def test_engine_inserts_requirement_todo_and_retries_original_todo(tmp_path) -> None:
    initial = PlanDraft(
        goal="统计实体数",
        todos=[_todo("count", "entity-agent", "统计当前场景中的实体数量")],
    )
    requirement = PlanDraft(
        goal="满足场景前置条件",
        todos=[
            _todo(
                "scene",
                "scene-agent",
                "请先创建场景或者打开已有场景",
                source=WorkerTodoSource.REQUIREMENT,
            )
        ],
    )
    planner = FakePlanner(initial, requirement)
    planner.final_content = "当前场景共有 3 个实体。"
    executor = RequirementExecutor()
    engine, _ = await _engine(tmp_path, planner, executor)

    run = await engine.create_run(
        thread_id="thread_requirement",
        intent="请统计场景的实体数",
        scene_context=SceneContext(status="none"),
    )

    assert executor.tasks == [
        "统计当前场景中的实体数量",
        "请先创建场景或者打开已有场景",
        "统计当前场景中的实体数量",
    ]
    assert [step.worker for step in run.steps] == ["scene-agent", "entity-agent"]
    assert run.steps[0].source == WorkerTodoSource.REQUIREMENT
    assert run.steps[0].generated_for_step_id == run.steps[1].step_id
    assert run.steps[0].requirement_key == "scene.opened"
    assert run.steps[1].attempt_count == 2
    assert run.status == RunStatus.SUCCEEDED
    assert run.final_result is not None
    assert run.final_result.summary == "当前场景共有 3 个实体。"


async def test_repeated_requirement_for_same_todo_fails_as_no_progress(tmp_path) -> None:
    initial = PlanDraft(
        goal="统计实体数",
        todos=[_todo("count", "entity-agent", "统计实体数量")],
    )
    requirement = PlanDraft(
        goal="打开场景",
        todos=[
            _todo(
                "open",
                "scene-agent",
                "打开一个场景",
                source=WorkerTodoSource.REQUIREMENT,
            )
        ],
    )
    planner = FakePlanner(initial, requirement)

    class RepeatingExecutor:
        async def execute(self, run, step, execution_id):
            if step.worker == "scene-agent":
                return StepResult(
                    status="success",
                    code="SCENE_OPENED",
                    summary="已打开场景",
                    effects=["scene.opened"],
                )
            return StepResult(
                status="waiting_dependency",
                code="REQUIREMENT_UNSATISFIED",
                summary="仍然缺少场景",
                requirements=[WorkerRequirement(key="scene.opened", description="需要打开场景")],
            )

    engine, _ = await _engine(tmp_path, planner, RepeatingExecutor())
    run = await engine.create_run(
        thread_id="thread_repeat_requirement",
        intent="统计实体数量",
        scene_context=SceneContext(status="none"),
    )

    original = next(step for step in run.steps if step.source == WorkerTodoSource.USER_INTENT)
    assert original.status == StepStatus.FAILED
    assert original.error is not None
    assert original.error.code == "REQUIREMENT_PLANNING_FAILED"
    assert "重复 requirement 无进展" in original.error.message


async def test_requirement_cycle_blocks_original_todo(tmp_path) -> None:
    initial = PlanDraft(
        goal="统计实体数",
        todos=[_todo("count", "entity-agent", "统计实体数量")],
    )
    requirement = PlanDraft(
        goal="打开场景",
        todos=[
            _todo(
                "open",
                "scene-agent",
                "打开一个场景",
                source=WorkerTodoSource.REQUIREMENT,
            )
        ],
    )
    planner = FakePlanner(initial, requirement)

    class CyclicExecutor:
        async def execute(self, run, step, execution_id):
            return StepResult(
                status="waiting_dependency",
                code="REQUIREMENT_UNSATISFIED",
                summary="需要打开场景",
                requirements=[WorkerRequirement(key="scene.opened", description="需要打开场景")],
            )

    engine, _ = await _engine(tmp_path, planner, CyclicExecutor())
    run = await engine.create_run(
        thread_id="thread_requirement_cycle",
        intent="统计实体数量",
        scene_context=SceneContext(status="none"),
    )

    requirement_step, original = run.steps
    assert requirement_step.status == StepStatus.FAILED
    assert requirement_step.error is not None
    assert "requirement 依赖环" in requirement_step.error.message
    assert original.status == StepStatus.BLOCKED


async def test_requirement_without_other_worker_fails_deterministically(tmp_path) -> None:
    initial = PlanDraft(
        goal="统计实体数",
        todos=[_todo("count", "entity-agent", "统计实体数量")],
    )
    planner = FakePlanner(initial)

    class MissingDependencyExecutor:
        async def execute(self, run, step, execution_id):
            return StepResult(
                status="waiting_dependency",
                code="REQUIREMENT_UNSATISFIED",
                summary="需要其他 Worker",
                requirements=[WorkerRequirement(key="scene.opened", description="需要打开场景")],
            )

    catalog = WorkerCatalog([WorkerDefinition(name="entity-agent", description="实体能力")])
    engine, _ = await _engine(tmp_path, planner, MissingDependencyExecutor(), catalog=catalog)
    run = await engine.create_run(
        thread_id="thread_no_provider",
        intent="统计实体数量",
        scene_context=SceneContext(status="none"),
    )

    assert run.steps[0].status == StepStatus.FAILED
    assert run.steps[0].error is not None
    assert "没有 Worker 能提供 requirement" in run.steps[0].error.message


async def test_graph_messages_pair_task_calls_with_worker_results(tmp_path) -> None:
    draft = PlanDraft(
        goal="查询",
        todos=[_todo("count", "entity-agent", "统计实体数量")],
    )
    planner = FakePlanner(draft)
    executor = RecordingExecutor()
    engine, _ = await _engine(tmp_path, planner, executor)
    run = await engine.create_run(
        thread_id="thread_messages",
        intent="统计实体数量",
        scene_context=SceneContext(status="opened", scene_name="场景A"),
    )

    snapshot = await engine._graph.aget_state({"configurable": {"thread_id": f"workflow:{run.run_id}"}})
    messages = snapshot.values["messages"]

    assert [type(message) for message in messages] == [
        HumanMessage,
        AIMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "统计实体数量"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].additional_kwargs["message_kind"] == "worker_todo_list"
    dispatch = next(message for message in messages if isinstance(message, AIMessage) and message.tool_calls)
    result = next(message for message in messages if isinstance(message, ToolMessage))
    assert dispatch.tool_calls[0]["name"] == "task"
    assert dispatch.tool_calls[0]["id"] == result.tool_call_id
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].additional_kwargs["message_kind"] == "final_answer"


async def test_waiting_user_context_keeps_candidate_data(tmp_path) -> None:
    draft = PlanDraft(
        goal="打开场景",
        todos=[_todo("open", "scene-agent", "打开火箭场景")],
    )
    planner = FakePlanner(draft)

    class ClarifyExecutor:
        async def execute(self, run, step, execution_id):
            return StepResult(
                status="waiting_user",
                code="MISSING_REQUIRED_INFO",
                summary="查询到 4 个包含“火箭”的场景，请指定要打开的场景。",
                data={"candidates": ["场景0942_ 1个火箭_1个卫星关节动画", "火箭测试"]},
            )

    engine, _ = await _engine(tmp_path, planner, ClarifyExecutor())
    run = await engine.create_run(
        thread_id="thread_clarify",
        intent="打开火箭场景",
        scene_context=SceneContext(status="none"),
    )

    assert run.status == RunStatus.WAITING_USER
    assert run.waiting_context is not None
    assert run.waiting_context.data["candidates"] == [
        "场景0942_ 1个火箭_1个卫星关节动画",
        "火箭测试",
    ]


async def test_resume_missing_arguments_injects_waiting_data_into_task(tmp_path) -> None:
    draft = PlanDraft(
        goal="打开场景",
        todos=[_todo("open", "scene-agent", "打开火箭场景")],
    )
    planner = FakePlanner(draft)

    candidates = {"candidates": ["场景0942_ 1个火箭_1个卫星关节动画", "火箭测试"]}

    class ClarifyThenOpenExecutor:
        def __init__(self) -> None:
            self.tasks: list[str] = []

        async def execute(self, run, step, execution_id):
            self.tasks.append(step.task)
            if len(self.tasks) == 1:
                return StepResult(
                    status="waiting_user",
                    code="MISSING_REQUIRED_INFO",
                    summary="查询到 2 个包含“火箭”的场景，请指定要打开的场景。",
                    data=candidates,
                )
            return StepResult(
                status="success",
                code="SCENE_OPENED",
                summary="已打开场景",
                effects=["scene.opened"],
            )

    executor = ClarifyThenOpenExecutor()
    engine, _ = await _engine(tmp_path, planner, executor)
    run = await engine.create_run(
        thread_id="thread_clarify_resume",
        intent="打开火箭场景",
        scene_context=SceneContext(status="none"),
    )
    assert run.status == RunStatus.WAITING_USER

    resumed = await engine.resume_run(run.run_id, user_input="1")

    assert resumed.status == RunStatus.SUCCEEDED
    assert executor.tasks == ["打开火箭场景", "打开火箭场景"]
    # 恢复后的 step 携带用户补充与候选数据，供执行器拼装进重发任务文本
    step = resumed.steps[0]
    assert step.resume_user_input == "1"
    assert step.resume_payload is not None
    assert step.resume_payload["candidates"] == candidates["candidates"]
    assert step.resume_payload["code"] == "MISSING_REQUIRED_INFO"


async def test_plan_node_passes_thread_history_to_planner(tmp_path) -> None:
    # 第 1 轮：查询类 Run 正常完成，留下 final_result 摘要
    first_draft = PlanDraft(
        goal="查询场景",
        todos=[_todo("query", "scene-agent", "查询包含火箭的场景")],
    )
    first_planner = FakePlanner(first_draft)
    first_planner.final_content = "找到 4 个场景：1.场景0942_ 1个火箭_1个卫星关节动画 2.火箭测试"

    class QueryExecutor:
        async def execute(self, run, step, execution_id):
            return StepResult(
                status="success",
                code="SCENE_QUERIED",
                summary="找到 4 个场景：1.场景0942_ 1个火箭_1个卫星关节动画 2.火箭测试",
                data={"count": 4},
            )

    engine, _ = await _engine(tmp_path, first_planner, QueryExecutor())
    first_run = await engine.create_run(
        thread_id="thread_history",
        intent="查询包含火箭的场景",
        scene_context=SceneContext(status="none"),
    )
    assert first_run.status == RunStatus.SUCCEEDED

    # 第 2 轮：同 thread 新 Run，Planner 应收到上一轮的 intent + 摘要
    second_draft = PlanDraft(
        goal="打开并统计",
        todos=[_todo("open", "scene-agent", "打开场景 火箭测试")],
    )
    second_planner = FakePlanner(second_draft)
    engine._planner = second_planner
    await engine.create_run(
        thread_id="thread_history",
        intent="打开第二个",
        scene_context=SceneContext(status="none"),
    )

    assert second_planner.plan_history is not None
    history_text = "\n".join(second_planner.plan_history)
    assert "查询包含火箭的场景" in history_text
    assert "找到 4 个场景" in history_text
    # 本轮请求不应出现在历史里
    assert "打开第二个" not in history_text


async def test_engine_blocks_dependent_todo_after_failure(tmp_path) -> None:
    draft = PlanDraft(
        goal="复合失败",
        todos=[
            _todo("open", "scene-agent", "打开场景"),
            _todo("count", "entity-agent", "统计实体", depends_on=["open"]),
        ],
    )
    planner = FakePlanner(draft)
    executor = RecordingExecutor(fail_task="打开场景")
    engine, _ = await _engine(tmp_path, planner, executor)

    run = await engine.create_run(
        thread_id="thread_failure",
        intent="打开后统计",
        scene_context=SceneContext(status="none"),
    )

    assert executor.tasks == ["打开场景"]
    assert run.status == RunStatus.FAILED
    assert run.steps[1].status == StepStatus.BLOCKED


async def test_finalizer_failure_falls_back_to_worker_summary(tmp_path) -> None:
    draft = PlanDraft(
        goal="统计",
        todos=[_todo("count", "entity-agent", "统计实体")],
    )
    planner = FakePlanner(draft)
    planner.raise_on_finalize = True

    class CountExecutor:
        async def execute(self, run, step, execution_id):
            return StepResult(
                status="success",
                code="ENTITIES_LIST",
                summary="当前场景共有 3 个实体",
                data={"count": 3},
            )

    engine, _ = await _engine(tmp_path, planner, CountExecutor())
    run = await engine.create_run(
        thread_id="thread_fallback",
        intent="统计实体数",
        scene_context=SceneContext(status="opened", scene_name="场景A"),
    )

    assert run.final_result is not None
    assert run.final_result.summary == "当前场景共有 3 个实体"


async def test_generic_finalizer_answer_is_rejected(tmp_path) -> None:
    draft = PlanDraft(
        goal="统计",
        todos=[_todo("count", "entity-agent", "统计实体")],
    )
    planner = FakePlanner(draft)
    planner.final_content = "任务已完成。"

    class CountExecutor:
        async def execute(self, run, step, execution_id):
            return StepResult(
                status="success",
                code="ENTITIES_LIST",
                summary="当前场景共有 3 个实体",
                data={"count": 3},
            )

    engine, _ = await _engine(tmp_path, planner, CountExecutor())
    run = await engine.create_run(
        thread_id="thread_generic_finalizer",
        intent="统计实体数",
        scene_context=SceneContext(status="opened", scene_name="场景A"),
    )

    assert run.final_result is not None
    assert run.final_result.summary == "当前场景共有 3 个实体"
