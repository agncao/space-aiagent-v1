"""日志、可观测性与重试配置模型。"""

from pydantic import Field
from pydantic_settings import BaseSettings


class LoggingConfig(BaseSettings):
    """日志配置。"""

    level: str = "INFO"
    format: str = "json"
    console: bool = True
    file_enabled: bool = True
    file_dir: str = "./logs"
    file_max_bytes: int = 10 * 1024 * 1024
    file_backup_count: int = 10
    file_rotation: bool = True
    loggers: dict[str, str] = Field(default_factory=dict)


class ObservabilityConfig(BaseSettings):
    """可观测性配置（OTel + Langfuse）。

    enabled=false 时全局走 NoOp Tracer，业务零开销、零依赖。
    Span 批处理参数由 Langfuse SDK 默认值控制；如需调优，
    通过环境变量 LANGFUSE_FLUSH_AT / LANGFUSE_FLUSH_INTERVAL 设置。
    """

    enabled: bool = False
    service_name: str = "space-aiagent"
    service_version: str = "2.0.0"

    langfuse_endpoint: str = "http://localhost:3000/api/public/otel"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    sampler_ratio: float = 1.0


class RetryLLMConfig(BaseSettings):
    """LLM 调用重试配置。"""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0
    # ToolStrategy 结构化输出解析失败(ValidationError)是否重试，默认 false
    retry_on_parse_error: bool = False


class RetryToolConfig(BaseSettings):
    """工具调用重试配置。"""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0


class RetryConfig(BaseSettings):
    """失败恢复配置（Phase 1B）。

    enabled=false 时 RetryMiddleware 透传，零开销。
    与 observability.enabled 独立（retry 是业务恢复，observability 是观测）。
    """

    enabled: bool = True
    llm: RetryLLMConfig = Field(default_factory=RetryLLMConfig)
    tool: RetryToolConfig = Field(default_factory=RetryToolConfig)
