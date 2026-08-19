"""V2 Worker 的结构化步骤响应。"""

import json
from enum import StrEnum, auto
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from space_aiagent.models.workflow_schemas import WorkerRequirement


class ResponseCode(StrEnum):
    """Worker 可返回的领域结果编码。"""

    description: str

    def __new__(cls, value: str, description: str):
        member = str.__new__(cls, value)
        member._value_ = value
        member.description = description
        return member

    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return name

    NO_SCENE = auto(), "当前没有打开的场景"
    SCENE_CREATED = auto(), "场景成功创建"
    SCENE_RENAMED = auto(), "场景成功重命名"
    SCENE_DELETED = auto(), "场景成功删除"
    SCENE_QUERIED = auto(), "成功查询场景信息"
    SCENE_OPENED = auto(), "成功打开场景"

    ENTITIES_EMPTY = auto(), "实体查询成功，但当前场景中没有实体"
    ENTITIES_LIST = auto(), "实体查询成功，返回当前场景中的实体列表"
    ENTITIES_ADDED = auto(), "已成功添加一个或者多个实体"
    ENTITY_CREATED = auto(), "已成功创建并添加实体到当前场景"
    ENTITIES_CLEARED = auto(), "成功清除所有实体"

    OUT_OF_SCOPE = auto(), "用户请求超出能力范围"
    MISSING_REQUIRED_INFO = auto(), "执行用户目标所需的参数不完整，需要用户补充"
    LLM_UNAVAILABLE = auto(), "LLM 调用重试耗尽或发生不可重试错误，AI 服务暂时不可用"
    SKILL_ROUTING_FAILED = auto(), "Skill 路由不明确、调用失败或 Skill 内容无法加载"

    @classmethod
    def schema_description(cls) -> str:
        code_descriptions = "\n".join(f"- {member.value}: {member.description}" for member in cls)
        return f"响应信息编码，用于准确标识本步骤结果。请根据以下业务含义选择：\n{code_descriptions}"


class WorkerResponse(BaseModel):
    """单个 Worker 步骤的标准结构化输出。"""

    status: Literal["success", "error", "info", "confirm", "requires"] = Field(
        description=(
            "响应状态: success=操作成功, error=操作失败, info=信息查询, "
            "confirm=需要用户确认, requires=需要其他 Worker 先完成前置任务"
        )
    )
    code: ResponseCode = Field(description=ResponseCode.schema_description())
    summary: str = Field(description="步骤结果摘要")
    data: list[dict[str, Any]] | dict[str, Any] | None = Field(
        default=None,
        description=(
            "本步骤产生的结构化结果。仅包含工具实际返回的数据，不得推测；"
            "无结构化结果时返回 null；必须直接输出 JSON，不得二次序列化"
        ),
    )
    requirements: list[WorkerRequirement] = Field(
        default_factory=list,
        description="仅 status=requires 时填写的跨 Worker 前置要求",
    )

    @field_validator("data", mode="before")
    @classmethod
    def _normalize_json_encoded_data(cls, data: Any) -> Any:
        if not isinstance(data, str):
            return data
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return data
        return parsed if isinstance(parsed, (list, dict)) else data
