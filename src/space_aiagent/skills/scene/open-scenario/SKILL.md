---
name: open-scenario
description: >
  打开场景的标准操作流程技能包。当用户表达「打开场景 / 切换场景 / 进入场景 /
  打开 xxx 场景 /打开场景xxx」等意图，或指定一个已存在的场景名要求激活时加载此技能。流程内含两处
  Human-in-the-loop 中断点：多场景歧义时让用户选择要打开的场景、目标场景存在未保存变更时
  让用户确认是否保存。处理工具返回码 SCENE_UNSAVED_CHANGES / SCENE_NOT_FOUND。
---

# 打开场景技能（操作手册）

## 适用场景

- 用户明确要求打开 / 切换某个已存在的场景（如「打开测试场景」「切换到文昌发射场景」）
- 用户给出场景名，需要把前端当前已打开的场景切到该场景
- 后续要对该场景做实体 / 轨道操作前，先确保已经打开场景

> 本技能只负责「打开已存在的场景」。若用户想新建场景，使用 `create_scenario`（不走本流程）。

## 涉及工具

| 工具 | 作用 | 关键参数 |
|------|------|----------|
| `query_scenario` | 按名查询场景，写回 `scenario_query_results` 与 `current_scene_name` | `scene_name`（用户输入的场景名） |
| `open_scenario` | 打开（激活）指定场景 | `scene_name`、`is_save_on_change` |

`open_scenario` 的 `is_save_on_change` 语义（**理解清楚再调用**）：

| 取值 | 含义 |
|------|------|
| `None`（缺省） | 未知用户是否要保存当前场景的未保存变更，**首次打开一律用此值** |
| `true` | 用户已明确「要保存」当前场景变更 |
| `false` | 用户已明确「不保存」当前场景变更 |

## 工具返回码（来自前端 `tool_result`）

| 码 | 含义 | 处理 |
|----|------|------|
| `SCENE_OPENED`（success=true） | 打开成功 | 流程结束 ✅ |
| `SCENE_UNSAVED_CHANGES` | 当前已打开场景有未保存变更，需用户确认是否保存 | 进入中断点 2 |
| `SCENE_NOT_FOUND` | 目标场景不存在 | 流程结束（告知用户未找到） |

## 完整流程（6 步）

### 第 1 步：按名查询场景

从用户输入中提取场景名，调用：

```
query_scenario(scene_name="<用户提到的场景名>")
```

读取返回的 `data`（场景列表）。**不要**在这一步直接 `open_scenario`，先看查询结果数量分流。

### 第 2 步：按查询结果数量分流

#### 情况 A — 命中多个场景（`len(data) > 1`）→ 🔴 中断点 1：用户选择

存在多个匹配场景，不能擅自替用户决定打开哪一个。**触发 Human-in-the-loop**，向用户列出候选并等待选择：

```jsonc
interrupt({
    "is_custom": True,
    "interrupt_type": "hitl_select",    // 业务自定义 type，前端据此渲染选择 UI
    "message": "找到多个匹配场景，请选择要打开的场景：",
    "data": {"scene_info_list":list[space_aiagent.models.schemas.ScenarioInfo]},
})
```

用户在前端选择后，通过 `resume` 返回所选场景名。**收到 resume 后**：把用户选中的场景名带入第 3 步的 `open_scenario`。

> resume 期望返回值：`{ "scene_name": "<用户选中的场景名>" }`

#### 情况 B — 命中唯一场景（`len(data) == 1`）→ 直接进入第 3 步

用 `data[0]` 的场景名。

#### 情况 C — 未命中（`len(data) == 0`）→ 流程结束

告知用户「未找到名为 xxx 的场景」，结束。不进入第 3 步。

### 第 3 步：首次打开（不带保存决策）

用第 2 步确定的场景名，**`is_save_on_change` 留空（None）**：

```
open_scenario(scene_name="<确定的目标场景名>")
```

### 第 4 步：处理 `open_scenario` 返回码

根据 `tool_result.code` 分流：

- **`SCENE_UNSAVED_CHANGES`** → 🔴 进入中断点 2
- **`SCENE_NOT_FOUND`** → 第 5 步
- **成功（`SCENE_OPENED` / `success=true`）** → 第 6 步

### 第 5 步：`SCENE_NOT_FOUND` → 流程结束

目标场景不存在（极少发生于查询命中之后，通常是并发删除等边界情况）。告知用户「场景 xxx 不存在或已被删除」，流程结束。

### 🔴 中断点 2：未保存变更确认

`open_scenario` 返回 `SCENE_UNSAVED_CHANGES`，说明当前已打开场景有未保存变更，切换会丢失。**必须得到明确的 Y / N 答案**，不能替用户假设。触发 Human-in-the-loop：

```jsonc
interrupt({
    "is_custom": True,
    "interrupt_type": "hitl_yn",
    "message": "当前场景存在未保存的变更，是否在切换前保存？(Y/N)",
    "data": {
        "scene_name": "<即将被切走的当前场景名>",
        "target_scene_name": "<即将打开的目标场景名>",
    },
})
```

用户 Y/N 决策通过 `resume` 返回。**收到 resume 后**，按答案带上 `is_save_on_change` 重新打开：

```
# 用户选「保存」(Y)
open_scenario(scene_name="<目标场景名>", is_save_on_change=true)

# 用户选「不保存」(N)
open_scenario(scene_name="<目标场景名>", is_save_on_change=false)
```

> resume 期望返回值：`{ "save_on_change": true | false }`

这次重试通常直接成功（`SCENE_OPENED`）。若仍返回 `SCENE_UNSAVED_CHANGES`（异常，理论上不应发生），向用户报错并结束，不要无限循环重试。

### 第 6 步：打开成功 → 流程结束

`open_scenario` 返回成功，`current_scene_name` 已由工具写回 state。向用户简要确认「已打开场景 xxx」，流程结束。

## 流程总览

```
query_scenario(scene_name)
        │
        ▼
   ┌────┴────┐
   │ 数量?   │
   └────┬────┘
        ├─ 0  ───────────────────────────► 告知未找到 → 结束
        ├─ >1 ─► 🔴 中断点1(选择) ─► resume(scene_name)
        └─ 1  ─────────────────────────────┐
                                            ▼
                          open_scenario(scene_name, is_save_on_change=None)
                                            │
                              ┌─────────────┼──────────────┐
                              ▼             ▼              ▼
                    SCENE_UNSAVED    SCENE_NOT_FOUND     成功
                    _CHANGES             │                │
                        │                │                │
              🔴 中断点2(Y/N)            └─► 告知不存在 ─► 结束
                        │
                  resume(save_on_change)
                        │
                        ▼
        open_scenario(scene_name, is_save_on_change=Y/N)
                        │
                        ▼
                     成功 ───────────────────────────► 告知已打开 → 结束
```

## 关键原则

- **先查后开**：绝不跳过 `query_scenario` 直接 `open_scenario`，否则无法处理多场景歧义。
- **首次打开不带保存决策**：第 3 步一律 `is_save_on_change=None`，把「是否保存」的决策权交给用户（中断点 2），不要默认 `false` 丢数据、也不要默认 `true` 擅自保存。
- **两个中断点不可跳过**：多场景选择、未保存变更确认都是用户决策，不可由 LLM 代答。
- **不无限重试**：中断点 2 重试后若仍 `SCENE_UNSAVED_CHANGES`，报错结束而非循环。
