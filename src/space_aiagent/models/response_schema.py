"""
Agent 结构化响应数据模型

定义 Agent 回复的标准 JSON 结构，确保相同场景输出一致的响应格式。
"""

import json
import logging
from enum import StrEnum, auto
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from space_aiagent.tools.registry import current_suggestion_candidates_var

logger = logging.getLogger(__name__)


class ResponseCode(StrEnum):
    """Agent 响应场景编码 — 单一数据源

    用 auto() + _generate_next_value_ 让 value 自动等于成员名（保留大写），
    写法类似 Java enum（只声明名字，不写值）。

    注意：StrEnum 默认 _generate_next_value_ 会返回 name.lower()，
    此处覆盖为返回 name 原文，与 response_templates.yaml 的大写键对齐。

    新增 code 时务必同步：
    1. config/response_templates.yaml（不然会走 _fallback_text）
    2. prompts/*.md（告诉 LLM 何时用）
    """

    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return name  # 覆盖 StrEnum 默认的 name.lower()，保持大写以匹配 yaml 键

    # 场景生命周期
    NO_SCENE = auto()
    TASK_LOOP_GUARD = auto()
    SCENE_CREATED = auto()
    SCENE_RENAMED = auto()
    SCENE_DELETED = auto()

    # 实体生命周期
    ENTITIES_EMPTY = auto()
    ENTITIES_LIST = auto()
    ENTITIES_ADDED = auto()
    ENTITY_CREATED = auto()
    ENTITIES_CLEARED = auto()

    # 能力外（用户请求超出当前可用工具范围）
    OUT_OF_SCOPE = auto()


class AgentResponse(BaseModel):
    """Agent 结构化响应

    所有子 Agent 最终回复使用此结构，由 ResponseRenderer 渲染为自然语言。
    """

    status: Literal["success", "error", "info", "confirm"] = Field(
        description="响应状态: success=操作成功, error=操作失败, info=信息查询, confirm=需要确认"
    )
    code: ResponseCode = Field(
        description="场景编码，用于匹配渲染模板。如 NO_SCENE, ENTITIES_LIST, SCENE_CREATED"
    )
    summary: str = Field(description="一句话摘要，模板未命中时作为降级展示")
    args: dict | None = Field(
        default=None,
        description="JSON格式的数据对象，用于填充模板变量（如 {'count': 5, 'entities': [...]}），"
        "当命中模板时，请与模板变量的命名保持一致",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="给用户的下一步建议列表",
    )

    @field_validator("args", mode="before")
    @classmethod
    def _parse_details(cls, v: dict | str | None) -> dict | None:
        """容错解析 details 字段，LLM 可能输出 JSON 字符串而非对象"""
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return None

    @field_validator("suggestions", mode="after")
    @classmethod
    def _filter_out_of_scope_suggestions(cls, v: list[str]) -> list[str]:
        """过滤掉能力范围外的 suggestions，避免误导用户

        LLM 偶尔会破 prompt 规则生成越界建议（如「添加月球探测器轨道」，
        但项目根本没开发此工具）。此处作为兜底：按当前 agent 工具组生成的
        候选集（从工具 description 首句提取）做子串双向匹配过滤。

        候选集由 ToolValidationMiddleware.awrap_model_call 在每个 LLM 调用前
        注入 ContextVar。未设置上下文（启动期/单元测试）时跳过过滤。
        """
        if not v:
            return v
        candidates = current_suggestion_candidates_var.get()
        if not candidates:
            return v
        # 子串双向匹配：建议包含候选 OR 候选包含建议
        logger.debug("原始 suggestions: %s，候选集: %s", v, candidates)
        kept = [s for s in v if any(c in s or s in c for c in candidates)]
        logger.debug("suggestions 中匹配候选推荐的有: %s", kept)
        filtered_out = set(v) - set(kept)
        if filtered_out:
            logger.warning("过滤越界 suggestions: %s", filtered_out)
        return kept
