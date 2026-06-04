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
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _resolve_env_vars(value: Any) -> Any:
    """
    递归解析 YAML 值中的 ${VAR:default} 语法

    示例:
        "${SERVER_HOST:0.0.0.0}" -> 从环境变量读取 SERVER_HOST，默认值为 0.0.0.0
    """
    # TODO: 实现递归解析逻辑
    # 1. 如果 value 是字符串，检查是否匹配 ${...} 模式
    # 2. 提取变量名和默认值
    # 3. 从 os.environ 获取值，不存在则用默认值
    # 4. 如果 value 是 dict 或 list，递归处理
    return value


def _load_yaml_config(env: str = "dev") -> dict[str, Any]:
    """
    加载 YAML 配置文件

    步骤:
    1. 加载 config/application.yaml 作为基础配置
    2. 如果存在 config/{env}.yaml，合并覆盖基础配置
    3. 解析所有 ${VAR:default} 环境变量引用
    """
    # TODO: 实现 YAML 加载和合并逻辑
    return {}


class ServerConfig(BaseSettings):
    """服务器配置"""
    host: str = "0.0.0.0"
    port: int = 8028
    workers: int = 1
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class LoggingConfig(BaseSettings):
    """日志配置"""
    level: str = "INFO"
    format: str = "json"       # json | console
    console: bool = True
    file_enabled: bool = True
    file_dir: str = "./logs"
    file_max_bytes: int = 10 * 1024 * 1024  # 10MB
    file_backup_count: int = 10
    file_rotation: bool = True


class LLMConfig(BaseSettings):
    """LLM 配置"""
    provider: str = "deepseek"   # deepseek | dashscope
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-plus"
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
        settings.llm.provider
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
        # TODO: 实现完整的配置加载流程
        # 1. load_dotenv(PROJECT_ROOT / ".env")
        # 2. env = os.getenv("APP_ENV", "dev")
        # 3. yaml_config = _load_yaml_config(env)
        # 4. 合并配置创建 Settings
        _settings = Settings()
    return _settings
