---
name: analyze-entity-data
description: >
  在当前航天场景中显示或隐藏实体分析数据的标准流程。当用户要求以表格、报表、图表或曲线
  展示卫星、地面站等当前实体的姿态、轨道、位置、地形、光照或太阳相对几何数据时使用。
  首次执行必须先查询候选并等待用户在前端选择一行；恢复后使用所选结构化数据执行分析。
  不用于创建实体、修改轨道、可见性计算或导出报告文件。
allowed-tools: query_analysis_item analyze_entity_data
metadata:
  enforcement: required
---

# 分析实体数据

本技能是严格的两阶段流程：

1. 首次执行：解析查询条件，调用 `query_analysis_item`，将完整候选返回给前端并等待选择。
2. 恢复执行：从“用户补充/补充数据”读取用户勾选的一行，直接调用
   `analyze_entity_data`，不得再次查询。

两个工具都操作前端当前选中的实体，不接受实体名称或实体 ID。不要把用户提到的卫星或地面站
名称拼入 `analysis_name`。工具返回缺少实体的 `MISSING_ARGUMENTS` 时，请用户先在场景中
选择目标实体，然后结束本轮。

## 0. 先判断是否为恢复执行

只要当前任务带有“用户补充”和“补充数据”，就进入“第 3 步：恢复后执行分析”。不得调用
`query_analysis_item`，也不得根据原始用户表达重新搜索。补充数据包含唯一有效选中行时执行
分析；没有有效选中行时继续等待用户选择。

只有任务完全不含恢复补充信息时，才按首次执行处理。

## 1. 首次执行：确定查询条件

### `analysis_name`

- 提取用户要求分析的数据项名称或关键词。例如“在场景中显示光照数据图表”提取为“光照”。
- 可以去掉“分析”“显示”“隐藏”“数据”“表格”“图表”等操作或展示词，但不得翻译专业术语、
  补充限定词或改写用户给出的关键词。
- 用户没有提供名称或关键词时，传 `None` 查询当前实体在指定展示形式下的全部候选。

### `show_kind`

只允许使用工具声明的精确值：

| 用户表达 | `show_kind` |
| --- | --- |
| 表格、表单、列表、明细、报表 | `Report` |
| 图形、图表、曲线、趋势、折线图等 | `Graph` |
| 未说明展示形式 | `Report` |

不得传 `table`、`chart`、`Chart` 或其他近义值。用户未指定展示形式时直接使用默认值
`Report`，不要追问。

### `is_show`

`is_show` 不传给查询工具，但必须从原始用户目标中保留，供恢复后调用分析工具：

- 显示、展示、打开、查看、分析某项数据 → `true`。
- 隐藏、关闭、移除某项数据显示 → `false`。
- `analyze_entity_data` 的 `is_show` 默认值是 `true`。没有明确要求隐藏时可以省略该参数，
  使用默认显示行为；明确要求隐藏时必须显式传入 `is_show=false`。

候选行中的 `is_show` 是该分析项查询时的当前显示状态，不是本次用户要求的目标状态。恢复后
不得用候选行的 `is_show` 覆盖从原始用户目标确定的值。

调用示例：

```text
# 用户：“在场景中显示光照数据图表”
query_analysis_item(
    analysis_name="光照",
    show_kind="Graph",
)
```

## 2. 查询后必须等待用户选择

查询工具返回体形如 `{success, code, data, message}`。当前前端的成功码为 `QUERY_SUCCESS`，
`data` 主要包含：

```json
{
  "entity_id": "satellite/LEO2LTO",
  "count": 2,
  "data": [
    {"analysisName": "光照时间", "type": "Graph", "is_show": false},
    {"analysisName": "光照强度", "type": "Graph", "is_show": false}
  ]
}
```

按结果分流：

- `QUERY_SUCCESS` 且 `items` 非空：无论候选是一项还是多项，都不得自行选择或调用
  `analyze_entity_data`。必须返回 `status=info`、`code=SELECTION_REQUIRED`，让工作流
  中断并等待用户在前端勾选一行。
- `QUERY_SUCCESS` 且 `items` 为空，或 `RESULT_EMPTY`：说明当前选中实体没有符合条件的
  分析项，结束，不调用分析工具。
- `NO_SCENE`：返回场景前置条件未满足，不自行打开或创建场景。
- `MISSING_ARGUMENTS`：使用 `message` 提示用户在场景中选择实体。
- `QUERY_FAILED`：使用 `message` 如实说明查询失败。
- 未知返回码：按 `success` 和 `message` 如实处理，不重试。

`SELECTION_REQUIRED` 必须在 `data` 中携带查询工具返回的完整候选结构，不得只写候选摘要，
不得遗漏、改写或重新排序 `items`。示例：

```json
{
  "status": "info",
  "code": "SELECTION_REQUIRED",
  "summary": "找到 2 个符合条件的数据分析项，请在前端勾选一项后继续。",
  "data": {
    "entity_id": "satellite/LEO2LTO",
    "count": 2,
    "items": [
      {"analysisName": "光照时间", "type": "Graph", "is_show": false},
      {"analysisName": "光照强度", "type": "Graph", "is_show": false}
    ]
  }
}
```

查询返回的 `analysisName` 是不透明标识符：保留全部空格、大小写、数字、括号、连字符和标点。
不得翻译 `Kepler`，不得增删“瞬时”“经典”等限定词，也不得使用用户关键词重建名称。

## 3. 恢复后执行分析

恢复任务会带有“用户补充”和“补充数据”。从补充数据中读取前端勾选的唯一结构化行：

- `analysis_name`：逐字符复制选中行的 `analysisName`；如果恢复协议已转为 snake_case，
  则读取 `analysis_name`。
- `show_kind`：使用选中行的 `type`。如果旧的恢复数据仍返回 `Chart`，调用工具前规范化为
  `Graph`；`Report` 和 `Graph` 保持不变。
- `is_show`：使用首次执行时从原始用户目标确定的目标状态，不使用候选行记录的当前状态。

只有补充数据能唯一确定一行，且名称非空、展示类型有效时才执行：

```text
# 显示：省略 is_show，使用默认值 true
analyze_entity_data(
    analysis_name="<逐字符复制选中行的 analysisName>",
    show_kind=<选中行确定的 Report/Graph>,
)

# 隐藏：显式传入 false
analyze_entity_data(
    analysis_name="<逐字符复制选中行的 analysisName>",
    is_show=false,
    show_kind=<选中行确定的 Report/Graph>,
)
```

如果用户没有选中、选中多行，或补充数据缺少有效的 `analysisName`/`analysis_name`，继续返回
`status=info`、`code=SELECTION_REQUIRED`，并在 `data` 中保留原候选，等待用户重新选择；
不得猜测、不得重新查询。

## 4. 分析结果处理

收到 `analyze_entity_data` 的结果前不得声称已经显示或隐藏数据。调用后按返回码处理并结束
本轮，不重试、不改名、不切换展示形式：

| `code` | 处理 |
| --- | --- |
| `ANALYZE_SUCCESS` | 使用 `message` 告知所选分析项已经按用户要求显示或隐藏。 |
| `RESULT_EMPTY` | 说明所选分析项已不存在或不可用。 |
| `MISSING_ARGUMENTS` | 使用 `message` 说明缺少实体或分析项名称。 |
| `ENTITY_NOT_FOUND` | 说明当前选中的实体不存在或已被移除。 |
| `NO_SCENE` | 返回场景前置条件未满足，不自行打开或创建场景。 |
| `ANALYZE_FAILED` | 使用 `message` 如实说明分析失败。 |

未知返回码按 `success` 和 `message` 如实说明。不要展示原始 JSON，也不要虚构返回码、实体、
候选、分析结果或成功状态。

## 必须遵守

- 首次执行只查询并产生 `SELECTION_REQUIRED`，不得调用分析工具。
- `SELECTION_REQUIRED.data` 必须携带查询返回的完整候选结构。
- 恢复执行直接使用前端选中行调用分析工具，不得再次查询。
- `analysis_name` 必须来自选中行，不得直接采用用户的查询关键词。
- `show_kind` 只允许 `Report` 或 `Graph`；未指定时默认 `Report`。
- `is_show` 来自原始用户目标；显示时默认 `true`，隐藏时显式传 `false`。候选行中的同名字段
  仅表示当前状态。
- 每次只能选择并分析一行；零行或多行选择必须继续等待用户。
