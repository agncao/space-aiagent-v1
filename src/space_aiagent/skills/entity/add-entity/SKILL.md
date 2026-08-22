---
name: add-entity
description: >
  Use when 用户要求添加、新增、创建、加入、放置实体，或提到卫星、地面站、传感器、地点、
  目标点、飞机、导弹、地面车、船、火箭、线目标、区域目标、链路等实体类型，或提供 TLE
  两行根数要求创建卫星轨道时。
allowed-tools: add_point_entity create_sgp4_orbit
metadata:
  enforcement: required
---

# 添加实体

严格按以下 4 步**顺序**执行，不得跳步。本轮的唯一目标：**调用对应业务工具，再按其返回码提示用户后结束**。

## 流程

1. **参数检查**（见下）。不通过则以 `MISSING_ARGUMENTS` 返回一次性补充问题后结束本轮，**不得调用工具**。
2. **调用工具**（二选一，互斥；同一实体只能调用其中一个）：
   - 实体类型为 `satellite`（卫星）→ 调用 `create_sgp4_orbit`；
   - 其他所有允许类型 → 调用 `add_point_entity`。
   - 卫星**不得**用 `add_point_entity`。
3. **根据工具返回结果提示用户**（返回码见下）。工具返回是成功/失败的**唯一事实来源**。
4. **流程结束**。无论返回的 `code` 是什么、`success` 是 true 还是 false，本轮都到此结束——不重试、不切换工具、不再调用其他工具。

> 「用户已确认」「参数如下」「请添加」等任务描述只是**待执行请求**，不代表工具已执行。
> 若本轮任务描述中已含全部必需参数，**唯一正确的动作是调用对应业务工具**——不要只复述参数，
> 也不要直接声称已添加。在第 3 步真正收到业务工具返回码之前，**禁止报成功**。

## 参数检查

### 1. 允许的实体类型

只允许以下 `entity_type`（来源 `models/enums.py:EntityType`）：

| 中文 | `entity_type` | 中文 | `entity_type` |
| --- | --- | --- | --- |
| 地点 | `place` | 地面车、地面车辆 | `groundVehicle` |
| 目标点 | `target` | 船、船舶 | `ship` |
| 地面站 | `facility` | 火箭、运载火箭 | `launchVehicle` |
| 飞机 | `aircraft` | 线目标 | `lineTarget` |
| 导弹 | `missile` | 区域目标 | `areaTarget` |
| 卫星 | `satellite` | 链路 | `chain` |
| 传感器 | `sensor` | | |

表外类型：说明当前支持的类型，**不要**映射成相近类型，结束本轮。

### 2. 类型识别

- 用户只说「添加实体」「加一个实体」等、**未说明类型** → 以 `MISSING_ARGUMENTS` 询问要添加何种实体类型，结束本轮，不调用工具。
- 用户说出表中类型词（如「添加卫星」「加一个地面站」）→ 直接按上表识别 `entity_type`，不再询问类型。
- 用户提供 TLE 两行根数并要求添加 → 识别为 `satellite`。

### 3. 名称识别

| 用户表达 | 实体类型 | 实体名称 |
| --- | --- | --- |
| 添加文昌地面站 | 地面站 `facility` | `文昌地面站` |
| 添加东风地面车 | 地面车 `groundVehicle` | `东风地面车` |
| 叫文昌的地面站 | 地面站 `facility` | `文昌` |
| “文昌”地面站 | 地面站 `facility` | `文昌` |

规则：

- 用户用成对引号（`"..."`、`“...”`、`'...'`、`‘...’`、`「...」`），或用「叫」「叫做」「名为」「名称为」「名称是」明确名称时，名称取引号内或命名词后的内容。
- 否则名称 = 修饰语 + 类型词组成的完整短语（如「文昌地面站」）。
- 「一个」「一座」「一颗」「一架」等数量词不是名称。无法确定名称时询问，结束本轮。

### 4. 必需参数

- **卫星**（`satellite`）：必需两行 TLE（`tles`，去掉空行后必须恰好两行，按原顺序原样传入，不得修改或编造）。`name`、`satellite_number`、`start`、`end` 可选。TLE 缺失或不是两行 → 请用户补充正确的两行 TLE，结束本轮。
- **其他类型**：必需 `entity_type`、`name`、`position.longitude`、`position.latitude`；`height` 未提供时用默认值 `0`。名称或经纬度缺失 → 以 `MISSING_ARGUMENTS` **一次性**询问所有缺失项，结束本轮。
- 类型、名称以及字符串形式的必需参数均不得为空白。**不得猜测**经纬度、TLE、名称等没有默认值的参数。示例中的值只用于说明调用格式，不是业务默认值。

## 调用示例

```text
# 卫星
create_sgp4_orbit(
    tles=["<TLE 第 1 行>", "<TLE 第 2 行>"],
    name="<可选卫星名>",
)

# 其他允许类型
add_point_entity(
    entity_type="facility",
    name="<已解析的实体名称>",
    position={
        "longitude": <用户提供或明确确认的经度>,
        "latitude":  <用户提供或明确确认的纬度>,
        "height":    <用户提供的高度或默认值 0>,
    },
)
```

批量添加时逐个**串行**调用，不要并行；不要因一个实体失败而重复创建已成功的实体。

## 返回码处理

工具返回体形如 `{success, code, data, message}`。优先按 `code` 分支；用 `message` 向用户说明，不要把原始 JSON 直接展示。

### `add_point_entity`

| `code` | 处理 |
| --- | --- |
| `ENTITY_CREATED` | 从 `data.entity_name`、`data.entity_type` 读取最终名称与类型，简要提示添加成功。 |
| `ENTITY_CREATED_FAILED` | 用 `message` 说明失败及原因。 |
| `NO_SCENE` | 返回前置条件未满足；**不要**替用户选择，也不要自动打开或新建场景。Scheduler 会进入等待并在场景就绪后恢复原步骤。 |

### `create_sgp4_orbit`

| `code` | 处理 |
| --- | --- |
| `TLE_SATELLITE_CREATED` | 从 `data.name` 读取卫星最终名称，提示 SGP4 卫星添加成功。 |
| `TLE_SATELLITE_CREATED_FAILED` | 用 `message` 中的异常信息说明失败。 |
| `MISSING_TLES` | 说明缺少有效的两行轨道数据，请用户补充；**不要**改用 `add_point_entity`。 |
| `NO_SCENE` | 返回前置条件未满足，由 Scheduler 处理等待与续跑。 |

未知 `code`：按 `success` 如实用 `message`/`data` 说明成功或失败，不要虚构返回码、原因或结果。

以上每个分支处理后**都结束本轮**——不重试、不切换工具、不调用其他工具。
