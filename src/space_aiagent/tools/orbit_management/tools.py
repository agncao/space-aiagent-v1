"""
轨道管理工具

基于 SGP4 模型的卫星轨道创建和更新。
工具通过远程桥接发送指令到前端 Cesium 执行。

桥接注入: 使用 bridge.bridge_var (ContextVar) 在会话级别注入 bridge 实例，
         由 websocket handler 在创建 Agent 前设置，工具函数通过 get() 获取。

前置条件: 场景必须已创建（由 ToolValidationMiddleware 在工具调用前校验）
"""

from langchain_core.tools import tool

from space_aiagent.bridge import bridge_var
from space_aiagent.models.schemas import OrbitUpdateParam, SGP4Param


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
    if  name:
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

    参数说明:
    - name: 要更新的卫星名称
    - color: 轨道颜色（十六进制，如 "#FF0000"）
    - glow_power: 发光强度
    - taper_power: 渐变强度
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
        tool_func="updateSGP4Orbit",
        args=args,
    )
