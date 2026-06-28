"""
Agent 响应渲染器

将 AgentResponse 结构化响应渲染为一致的自然语言回复。

模板与状态映射的单一数据源: config/response_templates.yaml
- DEFAULT_TEMPLATES: (code) → 模板字符串
- _REQUIRED_KEYS: code → 模板里 {var} 占位符集合（由 ResponseStabilizationMiddleware 用于校验补全）

稳定化职责统一由 ResponseStabilizationMiddleware 承担（在 agent 流程内修正 status/args）。
本模块只做纯渲染：模板命中 → format_map；未命中或缺字段 → 降级到 summary。
"""

import logging
import string
from pathlib import Path

import yaml

from space_aiagent.bridge import tools_results_var
from space_aiagent.infrastructure.config import CONFIG_DIR
from space_aiagent.models.response_schema import AgentResponse

logger = logging.getLogger(__name__)

_TEMPLATE_CONFIG_PATH: Path = CONFIG_DIR / "response_templates.yaml"


def  _load_template_config(
    path: Path = _TEMPLATE_CONFIG_PATH,
) -> tuple[
    dict[ str, str],
    dict[str, frozenset[str]],
]:
    """从 YAML 加载模板与状态映射

    YAML 格式: code → {status, template}
    返回 (DEFAULT_TEMPLATES, _REQUIRED_KEYS)：
    - DEFAULT_TEMPLATES: (code) → 模板字符串
    - _REQUIRED_KEYS: code → 模板里所有 {var} 占位符名集合（用 string.Formatter.parse 提取）

    缺失/格式错误时抛异常 —— 配置错误应在启动期暴露，不要拖到运行期。
    """
    with open(path, encoding="utf-8") as f:
        data: dict = yaml.safe_load(f) or {}

    templates: dict[str, str] = {}
    required_keys: dict[str, frozenset[str]] = {}
    for code, cfg in data.items():
        template = cfg["template"]
        templates[code] = template
        required_keys[code] = _extract_template_keys(template)
    return templates, required_keys


def _extract_template_keys(template: str) -> frozenset[str]:
    """用 string.Formatter.parse 提取模板里的 {var} 占位符名

    例：'场景 {scene_name} 共 {count} 个' → frozenset({'scene_name', 'count'})
    """
    keys: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(template):
        if field_name:
            # field_name 可能是 'var.attr' 或 'var[key]' 形式，取主名
            keys.add(field_name.split(".", 1)[0].split("[", 1)[0])
    return frozenset(keys)


# 模块加载时一次性读取配置；后续无需再读磁盘
DEFAULT_TEMPLATES, _REQUIRED_KEYS = _load_template_config()


class ResponseRenderer:
    """Agent 响应渲染器（纯渲染，不做任何修正）"""

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._templates = templates or DEFAULT_TEMPLATES.copy()

    @staticmethod
    def _fallback_text(response: AgentResponse) -> str:
        parts = [response.summary]
        if response.suggestions:
            parts.append(" **接下来您可以：**\n")
            parts.append("\n".join(f"- {s}" for s in response.suggestions))
        return "\n\n".join(parts)
        
    def render(self, response: AgentResponse) -> str:
        """将结构化响应渲染为自然语言
        """
        return self._fallback_text(response)
