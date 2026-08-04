---
name: add-entity
description: >
  Use when 用户要在场景中添加实体。支持类型：卫星(satellite)、地面站(facility)、
  传感器(sensor)、地点(place)、目标点(target)、飞机(aircraft)、导弹(missile)、
  地面车(groundVehicle)、船(ship)、火箭(launchVehicle)、线目标(lineTarget)、
  区域目标(areaTarget)、链路(chain)。触发词：添加/新增/创建/加入/放置 + 上述实体名，
  或用户提供 TLE 两行根数要求加卫星。
---

# 添加实体技能（操作手册）

## 适用场景

- 用户要求在当前场景中添加一个或多个实体（如「加一个文昌地面站」「添加一颗北斗卫星」「在这放个传感器」）
- 用户提供 TLE 两行根数，要求添加卫星
- 用户给出实体类型 + 名称 + 位置（经纬度），要求落点

> 本技能只负责「在已打开的场景里添加实体」。查询/清除实体直接用 `query_entities` / `clear_entities`（不走本流程）；改卫星轨道显示样式用 `update_sgp4_orbit`。

## 核心决策：用哪个工具？

添加实体只有两个工具，按「是否为卫星」二选一，**互斥——同一实体不要两个都调**：

| 实体类型 | 用哪个工具 | 必需参数 | 说明 |
|----------|-----------|----------|------|
| **卫星 satellite** | `create_sgp4_orbit` | `tles`（TLE 两行根数） | 卫星靠轨道数据驱动，用 SGP4 模型生成轨迹 |
| **其余全部类型**（地面站/传感器/地点/目标点/飞机/导弹/地面车/船/火箭/线目标/区域目标/链路） | `add_point_entity` | `entity_type`、`name`、`position`（经纬度/高度） | 静态点实体，靠地理位置定位 |

**判断规则**：
- 用户提供 TLE → 一定是卫星 → `create_sgp4_orbit`
- 用户说「卫星」但没给 TLE → **先向用户索要 TLE**（卫星必须有轨道根数，不能用 `add_point_entity` 顶替）
- 非卫星实体 → `add_point_entity`
- ⚠️ **同一颗卫星绝不要同时调两个工具**，否则会创建重复实体

## 涉及工具

| 工具 | 作用 | 关键参数 |
|------|------|----------|
| `add_point_entity` | 添加点实体（非卫星的全部类型） | `entity_type`（EntityType 枚举的字符串值）、`name`、`position{longitude, latitude, height}`、可选 `properties` |
| `create_sgp4_orbit` | 基于 SGP4 创建卫星轨道（添加卫星的唯一方式） | `tles`（list[str]，TLE 两行根数）、可选 `name`、`satellite_number`、`start`、`end` |

`EntityType` 枚举值（传给 `add_point_entity` 的 `entity_type`，用字符串值）：

| 中文 | 值 | 中文 | 值 |
|------|----|------|----|
| 地点 | `place` | 地面车 | `groundVehicle` |
| 目标点 | `target` | 船 | `ship` |
| 地面站 | `facility` | 火箭 | `launchVehicle` |
| 飞机 | `aircraft` | 线目标 | `lineTarget` |
| 导弹 | `missile` | 区域目标 | `areaTarget` |
| 卫星 | `satellite` | 链路 | `chain` |
| 传感器 | `sensor` | | |

> 注意：`satellite` 虽在枚举里，但**添加卫星走 `create_sgp4_orbit`，不走 `add_point_entity`**。

## 工具返回（来自前端 `tool_result`）

| `success` | 含义 | 处理 |
|-----------|------|------|
| `true` | 实体添加成功 | 流程结束 ✅ |
| `false` | 添加失败（TLE 非法、位置越界、重名等） | 读 `message` 向用户报错，按需修正参数后重试一次 |

> 若根本没有打开场景，工具调用会在前置校验（`ToolValidationMiddleware`）被拦截、不到达前端——回到第 1 步先打开场景。

## 完整流程（4 步）

### 第 1 步：确认场景已打开

读取系统消息中的「当前场景」字段（即 `current_scene_name`）：

- **非空**（如「当前场景: 测试场景」）→ 场景已打开，进入第 2 步
- **空 / None**（「当前场景: None」）→ 当前没有打开的场景，**不能直接添加实体**：
  - 实体必须落在已打开的场景里，故**先不要调用添加工具**（强行调用会被前置校验拦截）
  - 告知用户「需要先打开一个场景」，等场景打开后再添加
  - 打开场景走 open-scenario 技能（由 scene-agent 处理）；本 Agent 没有场景工具（`query_scenario` / `open_scenario`），不负责打开

> 实体必须落在某个已打开的场景里；没有场景时工具会被前置校验拦截。这一步是硬前提。

### 第 2 步：判断实体类型，选定工具

按「核心决策」表二选一：

- 卫星 → 走 `create_sgp4_orbit`，进入第 3a 步
- 非卫星 → 走 `add_point_entity`，进入第 3b 步

### 第 3a 步：添加卫星 → `create_sgp4_orbit`

收集 TLE 两行根数（必需）。用户未提供 TLE 时**先向用户索要**，不要用 `add_point_entity` 顶替。

```
create_sgp4_orbit(
    tles=["1 25544U 98067A   23001.50000000  .00000000  .00000-0  00000-0 0  9990",
          "2 25544  51.6440 000.0000 0000000 000.0000 000.0000 15.50000000123456"],
    name="<可选，卫星展示名>",
)
```

`start` / `end` / `satellite_number` 用户未指定则不传（用前端默认）。

### 第 3b 步：添加非卫星实体 → `add_point_entity`

收集实体类型、名称、位置（经纬度，高度可选默认 0）。位置缺失时先问用户。

```
add_point_entity(
    entity_type="facility",
    name="文昌地面站",
    position={"longitude": 110.95, "latitude": 19.61, "height": 0},
)
```

> 批量添加：逐个调用即可（**不要并行**——并行写同一场景状态会冲突，工具调用已强制关闭并行，按顺序串行调）。

### 第 4 步：按返回收尾

- 成功 → 简要告知用户「已添加 xxx」，流程结束
- 失败 → 读 `message` 报错，修正后重试一次；仍失败则如实告知用户

## 流程总览

```
        读取「当前场景」字段
              │
      ┌───────┴────────┐
   有场景             无场景
      │                │
      │         先打开场景（open-scenario 技能）
      │                │
      └────────┬───────┘
               ▼
         实体是卫星?
      ┌───────┴───────┐
     是                否
      │                │
      ▼                ▼
create_sgp4_orbit   add_point_entity
  (需 TLE)         (需 类型/名称/位置)
      │                │
      └────────┬───────┘
               ▼
       按返回收尾（成功→结束 / 失败→报错重试）
```

## 关键原则

- **先场景后实体**：没有打开的场景，添加会被前置校验拦截。先确认 `current_scene_name` 非空。
- **卫星只走轨道工具**：卫星必须用 `create_sgp4_orbit` + TLE；`add_point_entity` 的 `satellite` 类型不用于真正添加卫星。
- **两工具互斥**：同一实体（尤其卫星）不要同时调两个，否则重复创建。
- **缺参数先问**：卫星缺 TLE、点实体缺位置时先向用户索要，不要瞎填默认值（名称等确有默认值的字段除外）。
- **串行不并行**：批量添加按顺序逐个调用。
