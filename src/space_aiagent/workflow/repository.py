"""WorkflowRun Repository 及 SQLite 首版实现。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from space_aiagent.infrastructure.config import PROJECT_ROOT
from space_aiagent.models.workflow_schemas import TERMINAL_RUN_STATUSES, ToolExecution, WorkflowRun, utc_now


class ConcurrentRunUpdateError(RuntimeError):
    """Run revision 已变化。"""


class RunRepository(Protocol):
    """工作流运行记录仓库接口。

    定义工作流运行、步骤、工具执行及事件的持久化操作契约。
    实现类需保证线程安全与幂等性。
    """

    async def initialize(self) -> None:
        """初始化存储后端，建表或创建索引（幂等操作）。"""
        ...

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        """创建一条新的工作流运行记录。

        Args:
            run: 待创建的 WorkflowRun 实体。

        Returns:
            持久化后的 WorkflowRun（含服务端生成的字段）。
        """
        ...

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        """按 run_id 查询单条运行记录。

        Args:
            run_id: 运行记录唯一标识。

        Returns:
            匹配的 WorkflowRun，不存在时返回 None。
        """
        ...

    async def find_active_by_thread(self, thread_id: str) -> WorkflowRun | None:
        """查找指定 thread 下当前活跃的运行记录。

        Args:
            thread_id: 会话线程标识。

        Returns:
            活跃的 WorkflowRun，不存在时返回 None。
        """
        ...

    async def save_run(self, run: WorkflowRun, *, expected_revision: int) -> WorkflowRun:
        """乐观锁更新运行记录。

        Args:
            run: 更新后的 WorkflowRun 实体。
            expected_revision: 预期的当前 revision，用于冲突检测。

        Returns:
            更新后的 WorkflowRun。

        Raises:
            RevisionConflictError: revision 不匹配，存在并发冲突。
        """
        ...

    async def append_event(self, run: WorkflowRun, event_type: str, payload: dict[str, Any]) -> int:
        """向事件流追加一条事件。

        Args:
            run: 关联的运行记录。
            event_type: 事件类型标识。
            payload: 事件负载数据。

        Returns:
            新生成的事件 ID。
        """
        ...

    async def start_tool_execution(self, execution: ToolExecution) -> ToolExecution:
        """记录一次工具调用的开始。

        Args:
            execution: 待记录的工具执行实体。

        Returns:
            持久化后的 ToolExecution。
        """
        ...

    async def get_tool_execution_by_idempotency(self, idempotency_key: str) -> ToolExecution | None:
        """按幂等键查询工具执行记录，用于幂等去重。

        Args:
            idempotency_key: 调用方提供的幂等键。

        Returns:
            匹配的 ToolExecution，不存在时返回 None。
        """
        ...

    async def get_tool_execution_by_call_id(self, tool_call_id: str) -> ToolExecution | None:
        """按工具调用 ID 查询执行记录。

        Args:
            tool_call_id: 工具调用唯一标识。

        Returns:
            匹配的 ToolExecution，不存在时返回 None。
        """
        ...

    async def list_tool_executions(self, execution_id: str) -> list[ToolExecution]:
        """列出同一执行批次下的所有工具调用记录。

        Args:
            execution_id: 执行批次标识。

        Returns:
            ToolExecution 列表。
        """
        ...

    async def complete_tool_execution(self, tool_call_id: str, result: dict[str, Any]) -> ToolExecution | None:
        """标记工具执行完成并写入结果。

        Args:
            tool_call_id: 工具调用唯一标识。
            result: 执行结果负载。

        Returns:
            更新后的 ToolExecution，不存在时返回 None。
        """
        ...

    async def next_sequence(self, run_id: str) -> int:
        """获取 run 下的下一个自增序号。

        Args:
            run_id: 运行记录唯一标识。

        Returns:
            下一个可用的序号。
        """
        ...


class SqliteRunRepository:
    """不向上层暴露 SQL 的 SQLite Repository。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """初始化数据库，创建所需的表和索引（幂等操作）。仅首次调用执行建表：
        创建以下 5 张表：
        - workflow_runs：工作流运行记录
        - workflow_steps：工作流步骤记录
        - tool_executions：工具执行记录（含幂等键）
        - workflow_events：工作流事件流
        - workflow_sequences：运行序列号
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA foreign_keys=ON")
                await db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        run_id TEXT PRIMARY KEY,
                        thread_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_workflow_runs_thread
                        ON workflow_runs(thread_id, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS workflow_steps (
                        step_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
                        status TEXT NOT NULL,
                        ordinal INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_workflow_steps_run
                        ON workflow_steps(run_id, ordinal);
                    CREATE TABLE IF NOT EXISTS tool_executions (
                        tool_call_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        run_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        execution_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_tool_executions_step
                        ON tool_executions(run_id, step_id, created_at);
                    CREATE TABLE IF NOT EXISTS workflow_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_workflow_events_run
                        ON workflow_events(run_id, event_id);
                    CREATE TABLE IF NOT EXISTS workflow_sequences (
                        run_id TEXT PRIMARY KEY,
                        current_seq INTEGER NOT NULL
                    );
                    """
                )
                await db.commit()
            self._initialized = True

    @staticmethod
    def _json(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        await self.initialize()
        async with self._write_lock, aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT INTO workflow_runs(run_id,thread_id,status,revision,payload,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.thread_id,
                    run.status.value,
                    run.revision,
                    self._json(run),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            await self._replace_steps(db, run)
            await db.commit()
        return run

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            row = await (await db.execute("SELECT payload FROM workflow_runs WHERE run_id=?", (run_id,))).fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    async def find_active_by_thread(self, thread_id: str) -> WorkflowRun | None:
        await self.initialize()
        terminals = tuple(status.value for status in TERMINAL_RUN_STATUSES)
        placeholders = ",".join("?" for _ in terminals)
        query = (
            "SELECT payload FROM workflow_runs WHERE thread_id=? "
            f"AND status NOT IN ({placeholders}) ORDER BY updated_at DESC LIMIT 1"
        )
        async with aiosqlite.connect(self._db_path) as db:
            row = await (await db.execute(query, (thread_id, *terminals))).fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    async def save_run(self, run: WorkflowRun, *, expected_revision: int) -> WorkflowRun:
        await self.initialize()
        async with self._write_lock, aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute("SELECT revision FROM workflow_runs WHERE run_id=?", (run.run_id,))
            ).fetchone()
            if row is None:
                await db.rollback()
                raise KeyError(run.run_id)
            if row[0] != expected_revision:
                await db.rollback()
                raise ConcurrentRunUpdateError(f"run {run.run_id} revision={row[0]}, expected={expected_revision}")
            run.revision = expected_revision + 1
            run.updated_at = utc_now()
            await db.execute(
                "UPDATE workflow_runs SET status=?,revision=?,payload=?,updated_at=? WHERE run_id=?",
                (run.status.value, run.revision, self._json(run), run.updated_at.isoformat(), run.run_id),
            )
            await self._replace_steps(db, run)
            await db.commit()
        return run

    async def _replace_steps(self, db: aiosqlite.Connection, run: WorkflowRun) -> None:
        await db.execute("DELETE FROM workflow_steps WHERE run_id=?", (run.run_id,))
        for ordinal, step in enumerate(run.steps):
            await db.execute(
                "INSERT INTO workflow_steps(step_id,run_id,status,ordinal,payload,updated_at) VALUES(?,?,?,?,?,?)",
                (
                    step.step_id,
                    run.run_id,
                    step.status.value,
                    ordinal,
                    self._json(step),
                    step.updated_at.isoformat(),
                ),
            )

    async def append_event(self, run: WorkflowRun, event_type: str, payload: dict[str, Any]) -> int:
        await self.initialize()
        async with self._write_lock, aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO workflow_events(run_id,revision,event_type,payload,created_at) VALUES(?,?,?,?,?)",
                (run.run_id, run.revision, event_type, self._json(payload), utc_now().isoformat()),
            )
            await db.commit()
            return int(cursor.lastrowid or 0)

    async def start_tool_execution(self, execution: ToolExecution) -> ToolExecution:
        await self.initialize()
        async with self._write_lock, aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO tool_executions"
                "(tool_call_id,idempotency_key,run_id,step_id,execution_id,status,payload,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    execution.tool_call_id,
                    execution.idempotency_key,
                    execution.run_id,
                    execution.step_id,
                    execution.execution_id,
                    execution.status,
                    self._json(execution),
                    execution.created_at.isoformat(),
                    execution.updated_at.isoformat(),
                ),
            )
            await db.commit()
        existing = await self.get_tool_execution_by_idempotency(execution.idempotency_key)
        if existing is None:
            raise RuntimeError("tool execution 写入失败")
        return existing

    async def get_tool_execution_by_idempotency(self, idempotency_key: str) -> ToolExecution | None:
        return await self._get_tool_execution("idempotency_key", idempotency_key)

    async def get_tool_execution_by_call_id(self, tool_call_id: str) -> ToolExecution | None:
        return await self._get_tool_execution("tool_call_id", tool_call_id)

    async def list_tool_executions(self, execution_id: str) -> list[ToolExecution]:
        await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT payload FROM tool_executions WHERE execution_id=? ORDER BY created_at, tool_call_id",
                    (execution_id,),
                )
            ).fetchall()
        return [ToolExecution.model_validate_json(row[0]) for row in rows]

    async def _get_tool_execution(self, column: str, value: str) -> ToolExecution | None:
        await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            row = await (await db.execute(f"SELECT payload FROM tool_executions WHERE {column}=?", (value,))).fetchone()
        return ToolExecution.model_validate_json(row[0]) if row else None

    async def complete_tool_execution(self, tool_call_id: str, result: dict[str, Any]) -> ToolExecution | None:
        execution = await self.get_tool_execution_by_call_id(tool_call_id)
        if execution is None:
            return None
        execution.result = result
        execution.status = "succeeded" if result.get("success", False) else "failed"
        execution.updated_at = utc_now()
        async with self._write_lock, aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE tool_executions SET status=?,payload=?,updated_at=? WHERE tool_call_id=?",
                (execution.status, self._json(execution), execution.updated_at.isoformat(), tool_call_id),
            )
            await db.commit()
        return execution

    async def next_sequence(self, run_id: str) -> int:
        """为一个 Run 原子分配跨 SSE 重连单调递增的事件序号。"""
        await self.initialize()
        async with self._write_lock, aiosqlite.connect(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT OR IGNORE INTO workflow_sequences(run_id,current_seq) VALUES(?,0)",
                (run_id,),
            )
            await db.execute(
                "UPDATE workflow_sequences SET current_seq=current_seq+1 WHERE run_id=?",
                (run_id,),
            )
            row = await (
                await db.execute(
                    "SELECT current_seq FROM workflow_sequences WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            await db.commit()
        if row is None:
            raise RuntimeError("workflow sequence 分配失败")
        return int(row[0])


_repository: SqliteRunRepository | None = None


async def get_run_repository() -> RunRepository:
    """获取全局 RunRepository 单例，延迟初始化。

    Returns:
        RunRepository: 全局唯一的运行记录仓库实例。
    """
    global _repository
    if _repository is None:
        from space_aiagent.infrastructure.config import get_settings

        configured_path = Path(get_settings().workflow.database_path)
        db_path = configured_path if configured_path.is_absolute() else PROJECT_ROOT / configured_path
        _repository = SqliteRunRepository(db_path)
        await _repository.initialize()
    return _repository
