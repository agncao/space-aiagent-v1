"""
枚举类型定义

与前端 Cesium 场景实体类型对应
"""

from enum import StrEnum


class EntityType(StrEnum):
    """实体类型"""

    PLACE = "place"  # 地点
    TARGET = "target"  # 目标点
    FACILITY = "facility"  # 地面站
    AIRCRAFT = "aircraft"  # 飞机
    MISSILE = "missile"  # 导弹
    SATELLITE = "satellite"  # 卫星
    SENSOR = "sensor"  # 传感器
    GROUND_VEHICLE = "groundVehicle"  # 地面车
    SHIP = "ship"  # 船
    LAUNCH_VEHICLE = "launchVehicle"  # 火箭
    LINE_TARGET = "lineTarget"  # 线目标
    AREA_TARGET = "areaTarget"  # 区域目标
    CHAIN = "chain"  # 链路


class WSMessageType(StrEnum):
    """WebSocket 消息类型"""

    # 客户端 -> 服务端
    USER_INPUT = "user_input"  # 用户输入
    TOOL_RESULT = "tool_result"  # 前端工具执行结果

    # 服务端 -> 客户端
    AI_MESSAGE = "ai_message"  # AI 文本回复
    TOOL_CALL = "tool_call"  # 工具调用指令（前端执行）
    END = "end"  # 对话轮次结束
    ERROR = "error"  # 错误消息
