"""
Agent 结构化响应数据模型

定义 Agent 回复的标准 JSON 结构，确保相同场景输出一致的响应格式。
"""

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AgentResponse(BaseModel):
    """Agent 结构化响应

    所有子 Agent 最终回复使用此结构，由 ResponseRenderer 渲染为自然语言。
    """

    status: Literal["success", "error", "info", "confirm"] = Field(
        description="响应状态: success=操作成功, error=操作失败, info=信息查询, confirm=需要确认"
    )
    code: str = Field(
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
