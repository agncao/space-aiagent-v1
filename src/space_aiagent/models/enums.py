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
