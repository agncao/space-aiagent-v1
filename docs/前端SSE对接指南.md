# Space AI Agent V2 前端对接指南

本文是前端唯一有效协议。API 统一使用 `/api/v2/space`，传输为 SSE + HTTP POST。

## 1. 端点

### `GET /api/v2/space/health`

返回：

```json
{"status": "ok", "service": "space-aiagent"}
```

### `POST /api/v2/space/chat`

创建 Run，或在同 thread 存在 `waiting_user` Run 时续跑。响应类型为
`text/event-stream`。

```json
{
  "content": "打开火箭场景再添加文昌地面站",
  "thread_id": "thread-1",
  "message_id": "message-1",
  "current_scene_name": null,
  "scene_id": null,
  "scene_revision": 0,
  "mode": "continue"
}
```

`mode=replace` 会取消同 thread 的旧 waiting Run 并创建新 Run。同一 `thread_id`
已有活跃流时返回 `409`。

### `POST /api/v2/space/tool-result`

前端执行 Cesium 工具后回告：

```json
{
  "thread_id": "thread-1",
  "run_id": "run_x",
  "step_id": "step_x",
  "execution_id": "exec_x",
  "tool_call_id": "call_x",
  "idempotency_key": "idem_x",
  "tool_func": "addPointEntity",
  "args": {"name": "文昌地面站"},
  "success": true,
  "code": "ENTITY_CREATED",
  "message": "ok",
  "data": {"entity_id": "facility-1"},
  "scene_id": "scene-1",
  "scene_name": "火箭场景",
  "scene_revision": 2
}
```

前端必须按 `idempotency_key` 缓存已成功的副作用调用。重复请求不得再操作
Cesium，应直接回告原结果。

### Run 管理

- `GET /api/v2/space/runs/{run_id}`：获取权威快照。
- `POST /api/v2/space/runs/{run_id}/resume`：提交 `{"user_input":"...","data":{...}}`。
- `POST /api/v2/space/runs/{run_id}/cancel`：取消非终态 Run。

## 2. SSE 帧

```text
event: step_update
data: {"thread_id":"thread-1","run_id":"run_x","seq":3,"revision":2,"timestamp":"..."}
```

所有帧都包含 `thread_id`。V2 帧另外包含 `run_id`、`seq`、`revision`和
`timestamp`。工具帧还包含 `step_id`、`execution_id`、`tool_call_id`和
`idempotency_key`。

事件类型：

- 进度：`plan_snapshot`、`step_update`、`run_update`。
- 工具：`tool_start -> tool_args -> tool_result -> tool_end`。
- 中断：`interrupt`，随后以 `done {interrupted:true}` 关闭当前流。
- 终态：`done`、`error`。

前端按 `seq` 去重，按 `run_id + revision` 更新 TodoList。断线或刷新后使用
GET Run Snapshot 恢复，不根据帧数量推测进度。

## 3. 步骤结果

```json
{
  "step_id": "step_analysis",
  "input_bindings": {
    "facility_id": {
      "source_step_id": "step_add",
      "pointer": "/data/entity_id",
      "required": true
    }
  },
  "result": {
    "status": "success",
    "code": "ANALYSIS_COMPLETED",
    "summary": "分析完成",
    "data": {"window_count": 12},
    "artifacts": [
      {
        "artifact_id": "report-1",
        "kind": "report",
        "name": "可见性报告",
        "uri": "/artifacts/report-1",
        "media_type": "application/pdf",
        "metadata": {}
      }
    ]
  }
}
```

`waiting_context.result_ref` 指向权威步骤结果；`resolved_data` 是后端派生的前端
展示值，前端不应将它再回写为新结果。

## 4. 场景版本

- 每次场景切换或结构变化后递增 `scene_revision`。
- 工具回告必须携带执行后的 `scene_id`、`scene_name`和 `scene_revision`。
- 场景选择 resume 必须原样回传候选列表中的 `scene_name`。
