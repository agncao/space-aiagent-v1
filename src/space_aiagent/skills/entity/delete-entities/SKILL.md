---
name: delete-entities
description: >
  在当前场景中删除实体的标准流程。当用户要求删除、移除某个或某类名称匹配的实体，
  或要求清空、删除全部实体时使用。不用于删除场景本身，也不用于只查询或定位实体。
allowed-tools: delete_entities
metadata:
  enforcement: required
---

# 删除实体

本轮的唯一目标是根据用户明确给出的删除范围调用一次 `delete_entities`，并根据工具
返回结果提示用户后结束。删除不可撤销，工具调用会进入确认流程；不得绕过确认或提前
声称删除成功。

## 参数规则

- 用户明确给出实体名称或名称关键词时，将其作为 `entity_name` 原样传入。工具会删除
  名称模糊匹配的所有实体，因此回复中不得表述为“仅删除唯一实体”。
- 只有用户明确表达“全部实体”“所有实体”“清空实体”等全量删除意图时，才传
  `entity_name=""`。
- 用户只说“删除实体”但没有给出名称，也没有明确要求全部删除时，必须请求补充删除
  范围并结束本轮；禁止以默认空字符串调用工具。
- 不得把场景名称当作 `entity_name`。用户要删除场景时属于 scene-agent 的职责。

## 调用示例

```text
用户：删除 LEO2LTO
调用：delete_entities(entity_name="LEO2LTO")

用户：清空当前场景中的所有实体
调用：delete_entities(entity_name="")
```

收到工具返回后，以其 `success`、`code`、`message` 和 `data` 为唯一事实来源，简要说明
实际结果。调用失败时如实说明原因；参数未改变时不得重试。
