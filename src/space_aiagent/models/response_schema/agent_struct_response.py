"""
Agent 结构化响应数据模型

定义 Agent 回复的标准 JSON 结构，确保相同场景输出一致的响应格式。
"""

import json
from enum import StrEnum, auto
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.tools.registry import current_suggestion_candidates_var

logger = get_logger(__name__)


class ResponseCode(StrEnum):
    """Agent 响应场景编码 — 单一数据源

    每个成员同时声明编码和业务含义，schema_description() 会把它们写入
    AgentResponse.code 的 JSON Schema description，供 LLM 选择正确编码。

    注意：StrEnum 默认 _generate_next_value_ 会返回 name.lower()，
    此处覆盖为返回 name 原文，与 response_templates.yaml 的大写键对齐。

    新增 code 时务必同步：
    1. 在枚举成员旁声明清晰、互斥的业务含义
    2. 确定性短路响应需要同步 response_constants.SHORTCUT_RESPONSES
    """

    description: str

    def __new__(cls, value: str, description: str):
        member = str.__new__(cls, value)
        member._value_ = value
        member.description = description
        return member

    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return name  # 覆盖 StrEnum 默认的 name.lower()，保持大写以匹配 yaml 键

    # 场景生命周期
    NO_SCENE = auto(), "当前没有打开的场景"
    TASK_LOOP_GUARD = auto(), "主控 Agent 连续委派任务达到保护阈值，需要用户补充信息后再继续"
    SCENE_CREATED = auto(), "场景成功创建"
    SCENE_RENAMED = auto(), "场景成功重命名"
    SCENE_DELETED = auto(), "场景成功删除"
    SCENE_QUERIED = auto(), "成功查询场景信息"
    SCENE_OPENED = auto(), "成功打开场景"

    # 实体生命周期
    ENTITIES_EMPTY = auto(), "实体查询成功，但当前场景中没有实体"
    ENTITIES_LIST = auto(), "实体查询成功，返回当前场景中的实体列表"
    ENTITIES_ADDED = auto(), "已成功添加一个或者多个实体"
    ENTITY_CREATED = auto(), "已成功创建并添加实体到当前场景"
    ENTITIES_CLEARED = auto(), "成功清除所有实体"

    # 能力外（用户请求超出当前可用工具范围）
    OUT_OF_SCOPE = auto(), "用户请求超出能力范围"

    # 系统失败（LLM 调用重试耗尽 / 不可重试失败，由 RetryMiddleware 注入）
    LLM_UNAVAILABLE = auto(), "LLM 调用重试耗尽或发生不可重试错误，AI 服务暂时不可用"
    SKILL_ROUTING_FAILED = auto(), "Skill 路由不明确、调用失败或 Skill 内容无法加载"

    @classmethod
    def schema_description(cls) -> str:
        """生成会传给 LLM 的枚举值语义说明。"""
        code_descriptions = "\n".join(f"- {member.value}: {member.description}" for member in cls)
        return f"响应信息编码，用于准确标识本轮结果。请根据以下业务含义选择：\n{code_descriptions}"


class AgentResponse(BaseModel):
    """
    Agent 结构化响应, 所有子 Agent 最终回复使用此结构
    """

    status: Literal["success", "error", "info", "confirm"] = Field(
        description="响应状态: success=操作成功, error=操作失败, info=信息查询, confirm=需要确认"
    )
    code: ResponseCode = Field(description=ResponseCode.schema_description())
    summary: str = Field(description="核心摘要")
    data: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "本轮操作产生的结构化结果列表。仅包含工具实际返回的数据，不得推测或补充；"
            "无结构化结果时返回 null，即使只有一条记录也必须使用列表。"
            "必须直接输出 JSON 数组，不得把数组序列化成带引号的 JSON 字符串"
        ),
    )
    # suggestions: list[str] = Field(
    #     default_factory=list,
    #     description="给用户的下一步建议列表，最多两条",
    # )

    @field_validator("data", mode="before")
    @classmethod
    def _normalize_json_encoded_data(cls, data: Any) -> Any:
        """兼容 tool calling 将 data 数组二次序列化为 JSON 字符串的情况。"""
        if not isinstance(data, str):
            return data
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            # 保留原值，让 Pydantic 给出标准的 list_type 校验错误，避免吞掉坏数据。
            return data
        return parsed if isinstance(parsed, list) else data

    # @field_validator("suggestions", mode="after")
    # @classmethod
    # def _filter_out_of_scope_suggestions(cls, suggestions: list[str]) -> list[str]:
    #     """过滤掉能力范围外的 suggestions，避免误导用户
    #
    #     LLM 偶尔会破 prompt 规则生成越界建议（如「添加月球探测器轨道」，
    #     但项目根本没开发此工具）。此处作为兜底：按当前 agent 工具组生成的
    #     候选集（从工具 description 首句提取）做子串双向匹配过滤。
    #
    #     候选集由 ToolValidationMiddleware.awrap_model_call 在每个 LLM 调用前
    #     注入 ContextVar。未设置上下文（启动期/单元测试）时跳过过滤。
    #     """
    #     if not suggestions:
    #         return suggestions
    #     candidates = current_suggestion_candidates_var.get()
    #     if not candidates:
    #         return suggestions
    #     # 子串双向匹配：建议包含候选 OR 候选包含建议
    #     logger.debug("原始 suggestions", suggestions=suggestions, candidates=candidates)
    #     kept = [s for s in suggestions if any(c in s or s in c for c in candidates)]
    #     logger.debug("suggestions 匹配候选", kept=kept)
    #     filtered_out = set(suggestions) - set(kept)
    #     if filtered_out:
    #         logger.warning("过滤越界 suggestions", filtered_out=filtered_out)
    #     return kept
