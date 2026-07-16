"""
Pydantic 数据模型

定义所有业务实体的数据结构，用于工具参数校验和 API 请求/响应
"""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import EntityType

# ---- 场景相关 ----


class ScenarioConfig(BaseModel):
    """创建场景的参数"""

    scene_name: str = Field(default="新建场景", description="场景名称")
    central_body: str = Field(default="Earth", description="中心天体")
    start_time: str | None = Field(default=None, description="开始时间（ISO 8601）")
    end_time: str | None = Field(default=None, description="结束时间（ISO 8601）")
    description: str | None = Field(default=None, description="场景描述")


class ScenarioInfo(BaseModel):
    """提供给 Agent 和前端渲染器的场景查询结果。"""

    scene_name: str = Field(description="场景名称")
    update_time: str = Field(default="", description="最后更新时间")
    file_url: str = Field(default="", description="场景文件地址")
    uploader_name: str = Field(default="", description="上传人姓名")

    @classmethod
    def from_frontend(cls, payload: Mapping[str, Any]) -> "ScenarioInfo | None":
        """从前端原始对象提取展示字段，避免账户敏感字段进入 Agent 上下文。"""
        scene_name = str(payload.get("name") or payload.get("scene_name") or "").strip()
        if not scene_name:
            return None

        uploader = payload.get("uploader")
        uploader_name = str(payload.get("uploader_name") or "").strip()
        if isinstance(uploader, Mapping):
            uploader_name = str(uploader.get("name") or uploader.get("loginName") or "").strip()

        return cls(
            scene_name=scene_name,
            update_time=str(payload.get("updateTime") or payload.get("update_time") or "").strip(),
            file_url=str(payload.get("fileUrl") or payload.get("file_url") or "").strip(),
            uploader_name=uploader_name,
        )


class EntityPosition(BaseModel):
    """实体位置"""

    longitude: float = Field(description="经度")
    latitude: float = Field(description="纬度")
    height: float = Field(default=0, description="高度（米）")


# ---- 实体相关 ----


class EntityConfig(BaseModel):
    """创建实体的参数"""

    entity_type: EntityType = Field(description="实体类型")
    name: str = Field(description="实体名称")
    position: EntityPosition = Field(description="实体位置")
    properties: dict | None = Field(default=None, description="扩展属性")


class SGP4Param(BaseModel):
    """SGP4 轨道参数"""

    name: str | None = Field(default=None, description="在航天任务分析平台展示的卫星名称")
    satellite_number: str | None = Field(default=None, description="卫星编号")
    tles: list[str] = Field(description="TLE 两行根数")
    start: str | None = Field(default=None, description="开始时间")
    end: str | None = Field(default=None, description="结束时间")


class OrbitUpdateParam(BaseModel):
    """轨道更新参数"""

    name: str = Field(description="卫星名称")
    color: str | None = Field(default=None, description="轨道颜色（十六进制, 如 '#FF0000'）")
    glow_power: float | None = Field(default=None, description="发光强度")
    taper_power: float | None = Field(default=None, description="渐变强度")


# ---- API 请求/响应 ----


class InvokeRequest(BaseModel):
    """REST API 调用请求"""

    input: str = Field(description="用户输入")
    thread_id: str = Field(description="会话ID")


class InvokeResponse(BaseModel):
    """REST API 调用响应"""

    output: dict = Field(description="Agent 输出")
    thread_id: str = Field(description="会话ID")


# ---- 工具执行结果 ----


class ToolResult(BaseModel):
    """工具执行结果（前端返回）"""

    success: bool = Field(description="是否成功")
    message: str = Field(default="", description="结果消息")
    data: dict | list | None = Field(default=None, description="结果数据")


class SubagentClassification(BaseModel):
    """LLM 路由分类结构化输出

    PrimaryAgentMiddleware 自动续接 fallback 时用：当未从 task 历史捕获到
    subagent_type（流程 1：orchestrator 直接 NO_SCENE 不调 task）时，
    调 LLM 用此 schema 输出最匹配的子 agent name。
    """

    subagent_type: Literal["entity-agent", "scene-agent"] | None = Field(description="应该委派给的子 agent name")
