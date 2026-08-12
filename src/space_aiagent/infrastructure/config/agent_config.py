"""Agent 与基础运行配置模型。"""

from pydantic import Field
from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    """服务器配置。"""

    host: str = "0.0.0.0"
    port: int = 8028
    workers: int = 1
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class LLMConfig(BaseSettings):
    """LLM 配置（适用于所有 OpenAI 兼容接口）。"""

    api_key: str = ""
    base_url: str | None
    model: str | None
    temperature: float = 0.1
    streaming: bool = True
    enable_thinking: bool = False


class LLMFlashConfig(BaseSettings):
    """Flash LLM 配置（适用于所有 OpenAI 兼容接口）。"""

    api_key: str = ""
    base_url: str | None
    model: str | None
    temperature: float = 0.1
    streaming: bool = True
    enable_thinking: bool = False


class WorkflowConfig(BaseSettings):
    """V2 确定性工作流配置。"""

    enabled: bool = True
    database_path: str = "data/workflow.db"
