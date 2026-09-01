# 创建第一个 Project

Web Viewer 是常用入口，FastAPI 也提供同样的工作流。

## 浏览器流程

1. 按[快速开始](quickstart.md)启动应用后打开 `http://localhost:5678`。
2. 在 **Project Catalog** 输入名称并创建 Project。新 Project 初始状态为 `Draft`，
   `code/model.py` 为空骨架。
3. 在当前 Project 的消息框中描述完整的几何需求。写明单位、关键尺寸、孔或接口，
   以及结果是单个零件还是多部件产品。
4. 点击 **Send**。Project 进入 `Running`；Agent Harness 编写 Python、调用验证，并
   可根据失败证据修改源码。
5. 观察对话、进度事件和预览状态。成功后，Viewer 会显示已接受的 Scene、产品摘要、
   验证报告和下载入口。

## API 流程

创建 Project：

```bash
curl -sS -X POST http://localhost:8765/api/projects \
  -H 'content-type: application/json' \
  -d '{"name":"first-bracket"}'
```

使用返回的 `project_id` 提交任务：

```bash
curl -sS -X POST http://localhost:8765/api/projects/<project_id>/messages \
  -H 'content-type: application/json' \
  -d '{"message":"Build a 40 mm x 30 mm x 8 mm mounting plate with two 5 mm through holes.","request_id":"first-request"}'
```

响应会包含已持久化的 turn、Project 状态和 Scene 是否可用。轮询
`GET /api/projects/<project_id>`，或直接打开 Viewer 查看最终状态。

## 让需求足够明确

Agent 可以推断非关键尺寸，但必须保留用户的关键要求。应说明整体包络、材料或外观
假设、安装接口，以及多个独立制造件是否必须保留为 Assembly。
