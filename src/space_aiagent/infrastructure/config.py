"""
配置管理器 - 支持 YAML + .env + 多环境

设计思路:
1. 先加载 .env 文件中的环境变量
2. 加载 config/application.yaml 作为基础配置
3. 根据 APP_ENV 环境变量加载对应的环境覆盖配置（dev/staging/prod）
4. YAML 中的 ${VAR:default} 语法会被解析为环境变量

使用方式:
    from space_aiagent.infrastructure.config import get_settings
    settings = get_settings()
    print(settings.server.host)
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _resolve_env_vars(value: Any) -> Any:
    """
    递归解析 YAML 值中的 ${VAR:default} 语法

    示例:
        "${SERVER_HOST:0.0.0.0}" -> 从环境变量读取 SERVER_HOST，默认值为 0.0.0.0
    """
    if isinstance(value, str):

        def _replacer(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(var_name, default)

        return _ENV_VAR_PATTERN.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 覆盖 base"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml_config(env: str = "dev") -> dict[str, Any]:
    """
    加载 YAML 配置文件

    步骤:
    1. 加载 config/application.yaml 作为基础配置
    2. 如果存在 config/{env}.yaml，合并覆盖基础配置
    3. 解析所有 ${VAR:default} 环境变量引用
    """
    config: dict[str, Any] = {}

    base_path = CONFIG_DIR / "application.yaml"
    if base_path.exists():
        with open(base_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    env_path = CONFIG_DIR / f"{env}.yaml"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            env_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, env_config)

    return _resolve_env_vars(config)


class ServerConfig(BaseSettings):
    """服务器配置"""

    host: str = "0.0.0.0"
    port: int = 8028
    workers: int = 1
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class LoggingConfig(BaseSettings):
    """日志配置"""

    level: str = "INFO"
    format: str = "json"
    console: bool = True
    file_enabled: bool = True
    file_dir: str = "./logs"
    file_max_bytes: int = 10 * 1024 * 1024
    file_backup_count: int = 10
    file_rotation: bool = True
    loggers: dict[str, str] = Field(default_factory=dict)


class LLMConfig(BaseSettings):
    """LLM 配置（适用于所有 OpenAI 兼容接口）"""

    api_key: str = ""
    base_url: str|None
    model: str|None
    temperature: float = 0.1
    streaming: bool = True


class AgentConfig(BaseSettings):
    """Agent 配置"""

    max_iterations: int = 10


class Settings(BaseSettings):
    """
    全局配置 - 单例模式

    使用方式:
        settings = get_settings()
        settings.server.host
        settings.llm.model
    """

    app_name: str = "space-aiagent"
    app_version: str = "0.1.0"
    app_env: str = "dev"
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    model_config = {"env_prefix": "", "env_nested_delimiter": "__"}


_settings: Settings | None = None


def _apply_yaml_to_settings(yaml_config: dict[str, Any]) -> Settings:
    """将 YAML 配置映射到 Settings 结构"""
    flat: dict[str, Any] = {}

    app_cfg = yaml_config.get("application", {})
    flat["app_name"] = app_cfg.get("name", "space-aiagent")
    flat["app_version"] = app_cfg.get("version", "0.1.0")

    server_cfg = yaml_config.get("server", {})
    flat["server"] = ServerConfig(
        host=server_cfg.get("host", "0.0.0.0"),
        port=int(server_cfg.get("port", 8028)),
        workers=server_cfg.get("workers", 1),
        cors_origins=server_cfg.get("cors_origins", ["*"]),
    )

    log_cfg = yaml_config.get("logging", {})
    file_cfg = log_cfg.get("file", {})
    flat["logging"] = LoggingConfig(
        level=log_cfg.get("level", "INFO"),
        format=log_cfg.get("format", "json"),
        console=log_cfg.get("console", True),
        file_enabled=file_cfg.get("enabled", True),
        file_dir=file_cfg.get("dir", "./logs"),
        file_max_bytes=file_cfg.get("max_bytes", 10 * 1024 * 1024),
        file_backup_count=file_cfg.get("backup_count", 10),
        file_rotation=file_cfg.get("rotation", True),
        loggers=log_cfg.get("loggers", {}),
    )

    flat["app_env"] = os.getenv("APP_ENV", "dev")
    flat["agent"] = AgentConfig(max_iterations=yaml_config.get("agent", {}).get("max_iterations", 10))

    # LLM 配置从环境变量直接读取（.env 中的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）
    agent_cfg = yaml_config.get("agent", {})
    flat["llm"] = LLMConfig(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        temperature=float(os.getenv("LLM_TEMPERATURE", str(agent_cfg.get("temperature", 0.1)))),
        streaming=os.getenv("LLM_STREAMING", str(agent_cfg.get("streaming", True))).lower() == "true",
    )

    return Settings(**flat)


def get_settings() -> Settings:
    """
    获取全局配置单例

    步骤:
    1. 如果 _settings 已存在，直接返回
    2. 加载 .env 文件
    3. 加载 YAML 配置并合并环境变量
    4. 创建 Settings 实例并缓存
    """
    global _settings
    if _settings is None:
        load_dotenv(PROJECT_ROOT / ".env")
        env = os.getenv("APP_ENV", "dev")
        yaml_config = _load_yaml_config(env)
        _settings = _apply_yaml_to_settings(yaml_config)
    return _settings
