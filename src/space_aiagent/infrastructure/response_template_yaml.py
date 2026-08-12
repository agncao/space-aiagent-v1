"""响应模板 YAML 加载

从 config/response_templates.yaml 读取 code → 模板字符串映射。
当前仅 SHORTCUT_RESPONSES 把对应模板字符串写入 WorkerResponse.summary，
用于确定性前置条件与模型错误降级响应。
"""

from pathlib import Path

import yaml

from space_aiagent.infrastructure.config import CONFIG_DIR
from space_aiagent.infrastructure.logging import get_logger

logger = get_logger(__name__)

_TEMPLATE_CONFIG_PATH: Path = CONFIG_DIR / "response_templates.yaml"


def _load_template_config(path: Path = _TEMPLATE_CONFIG_PATH) -> dict[str, str]:
    """从 YAML 加载 code → template 字符串映射

    YAML 格式: code → {template: "..."}
    缺失/格式错误时抛异常 —— 配置错误应在启动期暴露，不要拖到运行期。
    """
    with open(path, encoding="utf-8") as f:
        data: dict = yaml.safe_load(f) or {}

    return {code: cfg["template"] for code, cfg in data.items()}


# 模块加载时一次性读取配置；后续无需再读磁盘
DEFAULT_TEMPLATES: dict[str, str] = _load_template_config()
