"""
数据库持久化模块

设计思路:
1. 使用 SQLite + aiosqlite 实现异步数据库访问
2. 用于持久化:
   - LangGraph 会话检查点（checkpoint）
   - 会话历史记录
3. 后续可无缝迁移到 PostgreSQL

使用方式:
    from space_aiagent.infrastructure.database import get_db
    db = await get_db()
"""

import os
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from space_aiagent.infrastructure.config import PROJECT_ROOT


class Database:
    """数据库管理器"""

    def __init__(self, database_url: str) -> None:
        """
        Args:
            database_url: 数据库连接字符串
                         SQLite: "sqlite+aiosqlite:///./data/space_aiagent.db"
        """
        self.database_url = database_url
        path = database_url.split("///")[-1]
        self.db_path = Path(path)
        self._db: aiosqlite.Connection | None = None
        self._checkpointer: AsyncSqliteSaver | None = None

    async def initialize(self) -> None:
        """
        初始化数据库连接和表结构
        """
        # 确保数据目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 打开 SQLite 连接
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        # LangGraph checkpoint 表由 AsyncSqliteSaver 自动创建

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._checkpointer:
            self._checkpointer = None
        if self._db:
            await self._db.close()
            self._db = None

    async def get_checkpointer(self) -> AsyncSqliteSaver:
        """
        获取 LangGraph 的 AsyncSqliteSaver checkpointer 实例
        """
        if self._checkpointer is None:
            if self._db is None:
                await self.initialize()
            self._checkpointer = AsyncSqliteSaver(conn=self._db)
            await self._checkpointer.setup()
        return self._checkpointer


_db: Database | None = None


async def get_db() -> Database:
    """
    获取数据库单例
    """
    global _db
    if _db is None:
        db_dir = os.path.join(PROJECT_ROOT, "data")
        os.makedirs(db_dir, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_dir}/space_aiagent.db"
        _db = Database(db_url)
        await _db.initialize()
    return _db
