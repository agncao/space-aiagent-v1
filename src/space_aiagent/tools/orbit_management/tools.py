"""
轨道管理工具

基于 SGP4 模型的卫星轨道创建和更新。
工具通过远程桥接发送指令到前端 Cesium 执行。

桥接注入: V2 SSE handler 在启动 WorkflowRun 前设置 bridge_var，Worker 工具通过 get() 获取。

前置条件: 场景必须已打开（由 Scheduler 和 WorkerToolValidationMiddleware 双重校验）
"""

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.models.biz_schemas import OrbitUpdateParam, SGP4Param

_NAMESPACE = "entity_tools"


@tool(args_schema=SGP4Param)
async def create_sgp4_orbit(
    tles: list[str],
    name: str | None = None,
    satellite_number: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """
    基于 SGP4 模型创建卫星轨道。
    """
    bridge = bridge_var.get()

    # 构建 SGP4 轨道参数
    args: dict = {
        "tles": tles,
    }
    if start:
        args["start"] = start
    if end:
        args["end"] = end
    if name:
        args["name"] = name
    if satellite_number:
        args["satelliteNumber"] = satellite_number

    # 前端对应方法: SceneTools.createSGP4Orbit(par)
    # 前端执行流程:
    #   1. TLEsFormatter.normalizeTLE 解析 TLE 数据
    #   2. postSgp4 发送到 SGP4 后端计算轨道数据
    #   3. 构建 CZML 格式的实体数据（位置/路径/可见时间）
    #   4. ProtoTreeData.dataSource.load(czml) 加载到场景
    #   5. 添加实体到场景树
    return await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func="createSGP4Orbit",
        args=args,
    )


@tool(args_schema=OrbitUpdateParam)
async def update_sgp4_orbit(
    name: str,
    color: str | None = None,
    glow_power: float | None = None,
    taper_power: float | None = None,
) -> dict:
    """
    更新卫星轨道的显示样式（颜色、发光、渐变）。
    """
    bridge = bridge_var.get()

    # 只传有值的参数，避免覆盖前端默认值
    args: dict = {"name": name}
    if color:
        args["color"] = color
    if glow_power is not None:
        args["glowPower"] = glow_power
    if taper_power is not None:
        args["taperPower"] = taper_power

    # 前端对应方法: SceneTools.updateSGP4Orbit(input)
    # 前端执行流程:
    #   1. 遍历 currentScenario.dataSource.entities.values 找到指定名称的卫星
    #   2. 设置 Cesium.PolylineGlowMaterialProperty（颜色、发光、渐变）
    #   3. solarSystem.requestRender() 刷新渲染
    return await bridge.send_tool_call(
        namespace=_NAMESPACE,
        tool_func="updateSGP4Orbit",
        args=args,
    )
