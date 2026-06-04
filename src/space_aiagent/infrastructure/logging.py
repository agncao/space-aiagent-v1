"""
结构化日志模块

设计思路:
1. 使用 structlog 实现结构化日志
2. 控制台输出: 开发环境用可读格式，生产环境用 JSON
3. 文件输出: JSON 格式 + 日志轮转（RotatingFileHandler）
4. 支持日志字段: timestamp, level, logger, message, caller, thread_id 等
5. 可接入 ELK 系统

使用方式:
    from space_aiagent.infrastructure.logging import get_logger
    logger = get_logger(__name__)
    logger.info("场景创建成功", scene_name="test", thread_id="xxx")
"""
import logging
import logging.handlers
from pathlib import Path

import structlog


def _add_caller_info(logger, method, event_dict):
    """
    structlog processor: 添加调用者信息（文件名:行号）

    TODO: 实现此 processor
    1. 从 event_dict 中获取 record（如果有的话）
    2. 提取 caller 文件名和行号
    3. 添加到 event_dict 中
    """
    return event_dict


def _setup_console_handler(fmt: str, level: str) -> logging.StreamHandler:
    """
    创建控制台日志处理器

    Args:
        fmt: 输出格式，"console" 为可读格式，"json" 为 JSON 格式
        level: 日志级别

    TODO: 实现
    1. 创建 StreamHandler
    2. 根据 fmt 选择 structlog 的 renderer
       - console: 使用 ConsoleRenderer(colors=True)
       - json: 使用 JSONRenderer()
    3. 设置日志级别
    """
    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    return handler


def _setup_file_handler(log_dir: str, max_bytes: int, backup_count: int, level: str) -> logging.handlers.RotatingFileHandler:
    """
    创建文件日志处理器（带轮转）

    Args:
        log_dir: 日志目录
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的备份文件数量
        level: 日志级别

    TODO: 实现
    1. 确保日志目录存在
    2. 创建 RotatingFileHandler
    3. 文件名格式: space-aiagent.log
    4. 设置 maxBytes 和 backupCount
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path / "space-aiagent.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    return handler


def setup_logging(
    level: str = "INFO",
    fmt: str = "console",
    console: bool = True,
    file_enabled: bool = False,
    file_dir: str = "./logs",
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 10,
) -> None:
    """
    初始化日志系统

    步骤:
    1. 配置标准库 logging 的 root logger
    2. 配置 structlog 的 processors
    3. 根据参数添加 console handler 和/或 file handler
    4. 设置 structlog 为全局日志工厂

    TODO: 完整实现
    1. 配置 structlog processors 链:
       - add_log_level
       - format_exc_info
       - UnicodeDecoder
       - _add_caller_info
       - TimestampFormatter (ISO 8601)
       - Renderer (根据 fmt 选择)
    2. 配置 standard library logging:
       - 获取 root logger
       - 设置 level
       - 添加 handlers
    3. 调用 structlog.configure()
    """
    pass


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    获取 logger 实例

    Args:
        name: 通常传 __name__

    使用:
        logger = get_logger(__name__)
        logger.info("消息", key1="value1")
    """
    return structlog.get_logger(name)
