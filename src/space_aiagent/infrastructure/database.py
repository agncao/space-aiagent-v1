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
from pathlib import Path


class Database:
    """
    数据库管理器

    TODO: 实现以下功能
    1. 初始化 SQLite 连接
    2. 创建必要的表（如果不存在）
       - checkpoints 表（LangGraph 检查点）
       - checkpoint_writes 表
       - checkpoint_blobs 表
    3. 提供连接获取方法
    4. 提供关闭方法
    """

    def __init__(self, database_url: str) -> None:
        """
        Args:
            database_url: 数据库连接字符串
                         SQLite: "sqlite+aiosqlite:///./data/space_aiagent.db"
        """
        self.database_url = database_url
        # TODO: 解析 database_url，提取数据库文件路径
        # TODO: 确保数据目录存在

    async def initialize(self) -> None:
        """
        初始化数据库连接和表结构

        步骤:
        1. 创建数据目录（如果不存在）
        2. 创建 SQLite 连接
        3. 执行建表 SQL（LangGraph checkpoint 相关表）
        """
        # TODO: 实现
        pass

    async def close(self) -> None:
        """关闭数据库连接"""
        # TODO: 实现
        pass

    def get_checkpointer(self):
        """
        获取 LangGraph 的 checkpointer 实例

        返回: SqliteSaver 或 AsyncSqliteSaver 实例

        TODO: 实现
        1. 从 langgraph-checkpoint-sqlite 导入 AsyncSqliteSaver
        2. 用当前连接创建 checkpointer
        """
        pass


_db: Database | None = None


async def get_db() -> Database:
    """
    获取数据库单例

    TODO: 实现
    1. 如果 _db 不存在，创建并初始化
    2. 返回 _db
    """
    global _db
    if _db is None:
        # TODO: 从配置读取 database_url
        _db = Database("sqlite+aiosqlite:///./data/space_aiagent.db")
        await _db.initialize()
    return _db
