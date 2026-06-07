"""
结构化日志模块

设计思路:
1. 使用 structlog 实现结构化日志
2. 控制台输出: 开发环境用 Spring 风格可读格式，生产环境用 JSON
3. 文件输出: JSON 格式 + 日志轮转（RotatingFileHandler）
4. 支持日志字段: timestamp, level, logger, message, caller, thread_name 等
5. 可接入 ELK 系统

使用方式:
    from space_aiagent.infrastructure.logging import get_logger
    logger = get_logger(__name__)
    logger.info("场景创建成功", scene_name="test", thread_id="xxx")
"""

import logging
import logging.handlers
import threading
from pathlib import Path

import structlog


def _add_caller_info(logger, method, event_dict):
    """structlog processor: 添加调用者信息（文件名:行号）"""
    record = event_dict.get("_record")
    if record:
        event_dict["caller"] = f"{record.filename}:{record.lineno}"
    return event_dict


def _add_thread_name(logger, method, event_dict):
    """structlog processor: 添加线程名"""
    record = event_dict.get("_record")
    if record:
        event_dict["thread_name"] = record.threadName
    else:
        event_dict["thread_name"] = threading.current_thread().name
    return event_dict


def _extract_fields_from_record(_, __, event_dict):
    """
    foreign_pre_chain processor: 为非 structlog 日志记录补齐标准字段。

    从 _record (logging.LogRecord) 中提取 timestamp / level / thread_name / caller，
    确保第三方库（uvicorn、openai 等）的日志也能显示完整的 Spring 风格格式。
    对于 structlog 日志，这些字段已在 processor 链中设置，此函数通过 setdefault 保持不覆盖。
    """
    record = event_dict.get("_record")
    if record is None:
        return event_dict
    event_dict.setdefault("timestamp", record.created)
    event_dict.setdefault("level", record.levelname)
    event_dict.setdefault("thread_name", record.threadName)
    event_dict.setdefault("caller", f"{record.filename}:{record.lineno}")
    return event_dict


def _console_renderer(_, __, event_dict):
    """
    Spring 风格控制台渲染器。

    格式: 2026-06-05 18:54:45.613 [INFO ] [MainThread] orchestrator.py:42 - 消息内容 key=value

    timestamp 可能来自 TimeStamper（ISO 字符串）或 foreign_pre_chain（epoch float），
    统一转为可读格式。
    """
    import datetime

    ts = event_dict.pop("timestamp", "")
    if isinstance(ts, (int, float)):
        ts = datetime.datetime.fromtimestamp(ts).isoformat()
    level = event_dict.pop("level", "")
    thread = event_dict.pop("thread_name", "")
    caller = event_dict.pop("caller", "")
    event = event_dict.pop("event", "")

    remaining = {k: v for k, v in event_dict.items()
                 if k not in ("_record", "_from_structlog", "logger")}
    extras = " " + " ".join(f"{k}={v}" for k, v in remaining.items()) if remaining else ""

    return f"{ts} [{level:<5}] [{thread}] {caller} - {event}{extras}"


def _setup_console_handler() -> logging.StreamHandler:
    """
    创建控制台日志处理器。

    handler 不设级别（默认 NOTSET = 接受所有），级别过滤由各 logger 自身控制。
    """
    return logging.StreamHandler()


def _setup_file_handler(
    log_dir: str, max_bytes: int, backup_count: int
) -> logging.handlers.RotatingFileHandler:
    """
    创建文件日志处理器（带轮转）。

    handler 不设级别（默认 NOTSET = 接受所有），级别过滤由各 logger 自身控制。
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    return logging.handlers.RotatingFileHandler(
        log_path / "space-aiagent.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )


def setup_logging(
    level: str = "INFO",
    fmt: str = "console",
    console: bool = True,
    file_enabled: bool = False,
    file_dir: str = "./logs",
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 10,
    loggers: dict[str, str] | None = None,
) -> None:
    """
    初始化日志系统

    配置 structlog processors 链和 standard library logging handlers。
    """
    # 选择 renderer：console 用 Spring 风格，否则用 JSON
    renderer = _console_renderer if fmt == "console" else structlog.processors.JSONRenderer()

    # structlog 共享 processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_thread_name,
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

    # 按包名单独设置日志级别（类似 Java logback 的 <logger>）
    for pkg_name, pkg_level in (loggers or {}).items():
        logging.getLogger(pkg_name).setLevel(getattr(logging, pkg_level.upper(), logging.WARNING))

    # 配置 standard library root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有 handlers
    root_logger.handlers.clear()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[_extract_fields_from_record],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    if console:
        console_handler = _setup_console_handler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if file_enabled:
        file_handler = _setup_file_handler(file_dir, file_max_bytes, file_backup_count)
        file_formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[_extract_fields_from_record],
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
