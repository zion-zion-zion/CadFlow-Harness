# API 面

`run.sh` 运行时，FastAPI 交互式 OpenAPI 文档位于 `http://localhost:8765/docs`。机器可读
schema 位于 `/openapi.json`，ReDoc 位于 `/redoc`。

## Project 与消息

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| `GET` | `/api/projects` | 列出 Project。 |
| `POST` | `/api/projects` | 使用 `{ "name": "..." }` 创建 Project。 |
| `GET` | `/api/projects/{project_id}` | 读取 Project 状态和产物摘要。 |
| `DELETE` | `/api/projects/{project_id}` | 确认名称后删除。 |
| `POST` | `/api/projects/{project_id}/messages` | 开启对话 turn；请求包含 `message`、`request_id`，可选 `retry_of` 和 `harness`。 |
| `GET` | `/api/projects/{project_id}/messages` | 读取对话 turn。 |
| `DELETE` | `/api/projects/{project_id}/conversation` | 确认后清空对话。 |
| `POST` | `/api/projects/{project_id}/stop` | 停止活动 Run。 |

旧的一次性路由 `/api/projects/{project_id}/run` 仍为 API 兼容而保留，接受 `prompt` 和可选
`harness`。

## 预览和产物

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| `GET` | `/api/projects/{project_id}/preview/status` | 读取实时预览状态。 |
| `GET` | `/api/projects/{project_id}/preview` | 下载最新可用的 GLB 预览。 |
| `POST` | `/api/projects/{project_id}/preview/retry` | 请求再次生成预览。 |
| `POST` | `/api/projects/{project_id}/preview/pause` | 暂停或恢复预览。 |
| `GET` | `/api/projects/{project_id}/scene` | 下载已接受的 `model.scene.zip`。 |
| `GET` | `/api/projects/{project_id}/product` | 读取已接受产品清单的 API 投影。 |
| `GET` | `/api/projects/{project_id}/product/manifest` | 下载 `product.json`。 |
| `GET` | `/api/projects/{project_id}/product/files/{role}` | 按 role 下载产品文件。 |
| `GET` | `/api/projects/{project_id}/product/part-step?part_id=...` | 下载某个独立 Part 的 STEP。 |

## 可观察性

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| `GET` | `/api/traces` | 列出有限的 Trace 摘要。 |
| `GET` | `/api/projects/{project_id}/events` | 以 Server-Sent Events 流式读取进度。 |
| `GET` | `/api/projects/{project_id}/trace` | 读取可分页、可过滤的 Trace。 |
| `GET` | `/api/projects/{project_id}/trace/download` | 下载脱敏 NDJSON。 |
| `GET` | `/api/projects/{project_id}/trace/events?cursor=...` | 读取一条 Trace 事件。 |
