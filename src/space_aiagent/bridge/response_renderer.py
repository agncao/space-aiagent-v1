"""
Agent 响应渲染器

将 AgentResponse 结构化响应渲染为一致的自然语言回复。

模板命中 → 模板渲染（措辞固定，数据动态填充）
模板未命中 → 用 summary + suggestions 组装通用回复
"""

import logging

from space_aiagent.models.response_schema import AgentResponse

logger = logging.getLogger(__name__)

# 默认模板注册表: (status, code) → 模板字符串
# 模板变量用 {key} 表示，从 AgentResponse.details 中取值
DEFAULT_TEMPLATES: dict[tuple[str, str], str] = {
    ("error", "NO_SCENE"): (
        "当前**尚未创建任何场景**，因此场景中没有任何实体（卫星、地面站、传感器等）。\n\n"
        "场景是所有航天任务实体的载体，需要先创建场景才能添加和管理实体。\n\n"
        "**接下来您可以：**\n"
        '- **创建场景** — 告诉我「创建场景」或「新建一个场景」，我会帮您处理\n'
        "- 场景创建后，即可添加卫星（基于 TLE）、地面站、传感器等实体\n\n"
        "请问需要先创建一个场景吗？"
    ),
    ("info", "ENTITIES_EMPTY"): (
        "当前场景中没有任何实体。\n\n"
        "**接下来您可以：**\n"
        "- 添加卫星（提供 TLE 两行根数）\n"
        "- 添加地面站、传感器等实体\n"
        "请告诉我您需要添加什么类型的实体。"
    ),
    ("success", "ENTITIES_LIST"): (
        "当前场景 **{scene_name}** 共有 **{count}** 个实体：\n\n"
        "{entity_list}"
    ),
    ("success", "SCENE_CREATED"): (
        "场景 **「{scene_name}」** 已创建成功！\n\n"
        "现在可以在此场景中添加实体了。\n\n"
        "**接下来您可以：**\n"
        "- 添加卫星 — 提供 TLE 两行根数，我会帮您创建轨道\n"
        "- 添加地面站、传感器等实体 — 告诉我类型和位置即可"
    ),
    ("success", "SCENE_CLEARED"): (
        "场景 **{scene_name}** 已清除所有内容。当前场景为空，可以重新创建实体。"
    ),
    ("success", "ENTITY_ADDED"): (
        "实体 **「{name}」**（类型: {entity_type}）已成功添加到场景 **{scene_name}** 中。"
    ),
}


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
        """
        将结构化响应渲染为自然语言

        优先匹配模板，未命中时用 summary 降级展示。
        """
        key = (response.status, response.code)
        template = self._templates.get(key)
        logger.debug("命中模板: (%s, %s)", response.status, response.code)

        if template is not None:
            details = response.details or {}
            try:
                result = template.format_map(_SafeDict(details))
                logger.debug("模板 (%s, %s)渲染成功: %s", response.status, response.code, result)
                return result
            except Exception:
                logger.debug("模板渲染失败: (%s, %s)", response.status, response.code)

        # 降级: 用 summary + suggestions 组装
        parts = [response.summary]
        logger.debug("未命中模板， 组装降级回复: (%s|%s)", response.summary, response.suggestions)
        if response.suggestions:
            parts.append("\n\n**接下来您可以：**")
            for s in response.suggestions:
                parts.append(f"- {s}")

        return "\n".join(parts)
