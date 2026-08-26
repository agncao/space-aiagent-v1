---
name: zoom-to
description: >
  在当前航天场景中选择并定位实体的标准流程。当用户要求定位到、缩放到、聚焦、跟踪、
  选择定位或查看某个卫星、地面站等实体时使用。首次执行必须按名称查询候选并等待用户在
  前端选择一项；恢复后使用所选候选的真实实体名称执行定位。不用于查询后直接列出实体、
  创建或删除实体，也不用于打开场景。
allowed-tools: zoom_to
metadata:
  enforcement: required
---

# 选择并定位实体

本技能是严格的两阶段流程：

1. 首次执行：从用户表达提取实体名称关键词，调用 `query_entities`，返回完整候选并等待前端选择。
2. 恢复执行：从“用户补充/补充数据”确定前端选中的唯一候选，使用候选中的真实
   `entity_name` 调用 `zoom_to`，不得再次查询。

即使查询只返回一个候选，首次执行也必须等待用户选择；不得自行定位。

## 0. 先判断是否为恢复执行

当前任务只要带有“用户补充”和“补充数据”，就直接进入“第 3 步：恢复后定位”。不得调用
`query_entities`，也不得根据原始用户表达重新搜索。

只有任务完全不含恢复补充信息时，才按首次执行处理。

## 1. 首次执行：提取查询关键词

将用户想定位的实体名称作为 `entity_name`，只去掉请求框架和实体类型提示，不得改写名称：

| 用户表达 | `entity_name` |
| --- | --- |
| 请定位到LEO2LTO | `LEO2LTO` |
| 请选择 LEO2LTO | `LEO2LTO` |
| 请定位到卫星LEO2LTO | `LEO2LTO` |
| 请选择定位实体LEO2LTO | `LEO2LTO` |
| 聚焦“LEO 2-LTO”卫星 | `LEO 2-LTO` |

提取规则：

- 成对引号（`"..."`、`“...”`、`'...'`、`‘...’`、`「...」`）中的内容优先作为完整名称。
- 否则只删除名称边界外的“请、选择、定位、定位到、缩放到、聚焦、查看、跟踪、实体、卫星”
  等操作词或类型词。
- 名称是待查询的不透明字符串。保留原有大小写、空格、数字、下划线、连字符和标点；不要翻译、
  分词、补全或纠正。
- 用户未提供可识别的名称关键词时，调用 `query_entities()` 查询全部实体，以便用户从全部候选中
  选择；不要追问名称。

```text
query_entities(entity_name="LEO2LTO")
```

## 2. 查询后返回候选并等待选择

查询工具返回体形如 `{success, code, data, message}`。当前前端成功结果的 `data` 通常为：

```json
{
  "entities": [
    {"entity_type": "satellite", "entity_name": "LEO2LTO", "entity_id": "satellite/LEO2LTO"}
  ],
  "count": 1
}
```

也兼容 `data` 直接为候选数组。只把对象中非空的 `entity_name` 视为有效候选，并按结果分流：

- `success=true` 且存在有效候选：不得调用 `zoom_to`。必须返回 `status=info`、
  `code=SELECTION_REQUIRED`，让工作流中断并等待用户在前端选择一项。
- `ENTITIES_EMPTY`、`success=true` 但没有有效候选：使用工具 `message` 说明未找到匹配实体，
  返回 `status=info`、`code=ENTITIES_EMPTY`，结束本轮。
- `NO_SCENE`：返回场景前置条件未满足；不要自行打开或创建场景。
- 其他失败：使用工具 `message` 如实说明查询失败，不重试，也不得调用 `zoom_to`。

`SELECTION_REQUIRED.data` 必须是对象，并完整保留有效候选：

- 工具 `data` 已是包含 `entities` 的对象时，原样携带其候选和 `count`。
- 工具 `data` 是数组时，包装成 `{"entities": <原数组>, "count": <实际数量>}`。
- 不得遗漏、改写、重新排序候选，也不得只返回候选摘要。

```json
{
  "status": "info",
  "code": "SELECTION_REQUIRED",
  "summary": "找到 1 个匹配实体，请在前端选择要定位的实体后继续。",
  "data": {
    "entities": [
      {"entity_type": "satellite", "entity_name": "LEO2LTO", "entity_id": "satellite/LEO2LTO"}
    ],
    "count": 1
  }
}
```

候选的 `entity_name` 和 `entity_id` 都是不透明标识符。展示、匹配和传参时必须逐字符复制，
不得增删空格或改变任何字符。

## 3. 恢复后定位

恢复任务会带有原候选以及前端提交的“用户补充/补充数据”。只能在以下信息能唯一确定原候选
中的一项时继续：

- 补充数据直接携带唯一选中行；
- 补充数据携带唯一选中的 `entity_id`，且能在原 `entities` 中精确匹配一项；
- 用户补充是序号或实体名称，且能在原 `entities` 中精确匹配一项。

字段使用 snake_case；如果恢复协议给出 camelCase，可对应读取 `entityName`、`entityId`。
最终的 `entity_name` 必须逐字符来自原候选，不能直接用模糊查询关键词或根据 ID 拼接名称。

唯一候选确定后调用一次：

```text
zoom_to(entity_name="<选中候选的真实 entity_name>")
```

如果没有选中、选中多项、序号越界，或补充信息不能唯一匹配原候选，继续返回
`status=info`、`code=SELECTION_REQUIRED`，并在 `data` 中保留原候选，等待用户重新选择；
不得猜测、不得重新查询。

## 4. 定位结果处理

收到 `zoom_to` 的结果前不得声称已经定位。调用后按返回码处理并结束本轮，不重试：

| `code` | 处理 |
| --- | --- |
| `ZOOM_TO_SUCCESS` | 返回 `status=success`、`code=ZOOM_TO_SUCCESS`，使用 `message` 告知已定位到所选实体。 |
| `ENTITY_NOT_FOUND` | 返回 `status=error`、`code=ENTITY_NOT_FOUND`，说明实体可能已被移除；不要重新查询或改名重试。 |
| `NO_VIEWER` | 返回 `status=error`、`code=NO_VIEWER`，说明视图尚未初始化。 |
| `NO_SCENE` | 返回场景前置条件未满足；不要自行打开或创建场景。 |
| `ZOOM_TO_FAILED` | 返回 `status=error`、`code=ZOOM_TO_FAILED`，使用 `message` 如实说明定位失败。 |

未知返回码按 `success` 和 `message` 如实说明，不要展示原始 JSON，也不要虚构实体、原因或成功状态。

## 必须遵守

- 首次执行只查询并产生 `SELECTION_REQUIRED`，不得调用 `zoom_to`。
- 即使只有一个候选，也必须等待前端选择。
- `SELECTION_REQUIRED.data` 必须是对象并携带完整候选数组。
- 恢复执行直接使用原候选中唯一选中项的真实 `entity_name`，不得再次查询。
- 每次只能选择并定位一个实体；零项或多项选择必须继续等待用户。
- `zoom_to` 只调用一次；任何结果返回后都结束本轮。
