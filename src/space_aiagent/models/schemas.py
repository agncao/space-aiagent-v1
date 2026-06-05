"""
Pydantic 数据模型

定义所有业务实体的数据结构，用于工具参数校验和 API 请求/响应
"""

from pydantic import BaseModel, Field

from .enums import EntityType

# ---- 场景相关 ----


class ScenarioConfig(BaseModel):
    """创建场景的参数"""

    name: str = Field(default="新建场景", description="场景名称")
    central_body: str = Field(default="Earth", description="中心天体")
    start_time: str | None = Field(default=None, description="开始时间（ISO 8601）")
    end_time: str | None = Field(default=None, description="结束时间（ISO 8601）")
    description: str | None = Field(default=None, description="场景描述")


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

    name: str = Field(description="卫星名称")
    tles: list[str] = Field(description="TLE 两行根数")
    start: str | None = Field(default=None, description="开始时间")
    end: str | None = Field(default=None, description="结束时间")


class OrbitUpdateParam(BaseModel):
    """轨道更新参数"""

    name: str = Field(description="卫星名称")
    color: str | None = Field(default=None, description="颜色（十六进制）")
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
