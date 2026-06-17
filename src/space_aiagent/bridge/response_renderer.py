"""
Agent 响应渲染器

将 AgentResponse 结构化响应渲染为一致的自然语言回复。

模板与状态映射的单一数据源: config/response_templates.yaml
- DEFAULT_TEMPLATES: (status, code) → 模板字符串
- _CODE_STATUS_MAP: code → 正确 status（B 方案 normalize 据此防止 LLM 漂移）

模板命中 → 模板渲染（措辞固定，数据动态填充）
模板未命中 → 用 summary + suggestions 组装通用回复
"""

import logging
from pathlib import Path

import yaml

from space_aiagent.infrastructure.config import CONFIG_DIR
from space_aiagent.models.response_schema import AgentResponse

logger = logging.getLogger(__name__)

_TEMPLATE_CONFIG_PATH: Path = CONFIG_DIR / "response_templates.yaml"


def _load_template_config(path: Path = _TEMPLATE_CONFIG_PATH) -> tuple[
    dict[tuple[str, str], str],
    dict[str, str],
]:
    """从 YAML 加载模板与状态映射

    YAML 格式: code → {status, template}
    返回 (DEFAULT_TEMPLATES, _CODE_STATUS_MAP)，消除 status 字段在两处的冗余。
    缺失/格式错误时抛异常 —— 配置错误应在启动期暴露，不要拖到运行期。
    """
    with open(path, encoding="utf-8") as f:
        data: dict = yaml.safe_load(f) or {}

    templates: dict[tuple[str, str], str] = {}
    code_status: dict[str, str] = {}
    for code, cfg in data.items():
        status = cfg["status"]
        template = cfg["template"]
        templates[(status, code)] = template
        code_status[code] = status
    return templates, code_status


# 模块加载时一次性读取配置；后续无需再读磁盘
DEFAULT_TEMPLATES, _CODE_STATUS_MAP = _load_template_config()


def normalize(response: AgentResponse) -> AgentResponse:
    """按 code 强制 status 一致，防止 LLM 漂移

    code 在 _CODE_STATUS_MAP 中且 status 不匹配时，返回新的 AgentResponse（status 已修正），
    并记 warning 便于监控漂移频率。未知 code 不动 status，避免误伤。
    """
    expected = _CODE_STATUS_MAP.get(response.code)
    if expected is None or response.status == expected:
        return response
    logger.warning(
        "AgentResponse 状态归一化: code=%s, LLM=%s -> %s",
        response.code,
        response.status,
        expected,
    )
    return response.model_copy(update={"status": expected})


class _SafeDict(dict):
    """安全的格式化字典，缺失键时返回占位符而非抛出 KeyError"""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


class ResponseRenderer:
    """Agent 响应渲染器"""

    def __init__(self, templates: dict[tuple[str, str], str] | None = None) -> None:
        self._templates = templates or DEFAULT_TEMPLATES.copy()

    def register_template(self, status: str, code: str, template: str) -> None:
        """注册新模板或覆盖已有模板"""
        self._templates[(status, code)] = template
        logger.debug("注册响应模板: (%s, %s)", status, code)

    def render(self, response: AgentResponse) -> str:
        """将结构化响应渲染为自然语言

        1. 按 (status, code) 查模板，命中则用 details 填充占位符
        2. 未命中或填充异常 → 降级用 summary
        3. suggestions 不自动追加（模板文本已含引导语，重复会冗余；该字段保留供前端 UI 单独取用）
        """
        key = (response.status, response.code)
        template = self._templates.get(key)

        if template is None:
            logger.debug("未命中模板 (%s, %s)，用 summary", response.status, response.code)
            return response.summary

        details = response.details or {}
        try:
            return template.format_map(_SafeDict(details))
        except Exception:
            logger.debug(
                "模板渲染异常 (%s, %s)，降级用 summary",
                response.status,
                response.code,
                exc_info=True,
            )
            return response.summary
