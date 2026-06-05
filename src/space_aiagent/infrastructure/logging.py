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
    """
    record = event_dict.get("_record")
    if record:
        event_dict["caller"] = f"{record.filename}:{record.lineno}"
    return event_dict


def _setup_console_handler(fmt: str, level: str) -> logging.StreamHandler:
    """
    创建控制台日志处理器
    """
    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    return handler


def _setup_file_handler(
    log_dir: str, max_bytes: int, backup_count: int, level: str
) -> logging.handlers.RotatingFileHandler:
    """
    创建文件日志处理器（带轮转）
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

    配置 structlog processors 链和 standard library logging handlers。
    """
    # 选择 renderer
    renderer = structlog.dev.ConsoleRenderer(colors=True) if fmt == "console" else structlog.processors.JSONRenderer()

    # structlog 共享 processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_caller_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # 配置 structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 配置 standard library root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有 handlers
    root_logger.handlers.clear()

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    if console:
        console_handler = _setup_console_handler(fmt, level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if file_enabled:
        file_handler = _setup_file_handler(file_dir, file_max_bytes, file_backup_count, level)
        file_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)


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
