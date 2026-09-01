# 配置

运行 `./setup.sh` 后，`.env` 会从 `.env.example` 创建。Shell 中导出的环境变量优先于 `.env` 中的值。

## 模型和 Agent 设置

| 变量 | 必填 | 生效值或默认值 | 用途 |
| --- | --- | --- | --- |
| `OPENAI_BASE_URL` | 否 | 留空时使用提供商默认值 | OpenAI 兼容 API Base URL。 |
| `OPENAI_MODEL_ID` | 是 | 未设置 | Agent Harness 的模型标识符。 |
| `OPENAI_API_KEY` | 是 | 未设置 | 提供商凭证，严禁提交。 |
| `OPENAI_REVIEW_MODEL_ID` | 否 | 留空时使用 `OPENAI_MODEL_ID` | 可选的 `cad_review` 模型。 |
| `OPENAI_REASONING_EFFORT` | 否 | `.env.example` 为 `medium`；未设置时不发送明确级别 | 可选 `none`、`low`、`medium`、`high`、`max`；设为 `none` 时使用 Chat Completions。 |
| `OPENAI_REASONING_SUMMARY` | 否 | 空 | Responses 摘要：`auto`、`concise` 或 `detailed`。 |
| `CADFLOW_AGENT_RUN_TIMEOUT_SECONDS` | 否 | `1200` | 单次 Run 墙钟预算。 |

## 持久化和预览

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CADFLOW_CONVERSATION_MAX_CONTEXT_CHARS` | `200000` | 传给 Agent 的最大对话上下文。 |
| `CADFLOW_ARTIFACT_VERSION_LIMIT` | `10` | 每个 Project 保留的 Accepted 产物版本数。 |
| `CADFLOW_PREVIEW_TIMEOUT_SECONDS` | 代码默认 `15`；`.env.example` 设置为 `60` | 实时预览 worker 的墙钟预算。 |
| `TEXT_TO_CAD_PROJECTS_ROOT` | `output/projects` | Project Catalog 和产物根目录。 |

## 网络设置

`run.sh` 使用以下默认值：

| 变量 | `run.sh` 默认值 | 用途 |
| --- | --- | --- |
| `TEXT_TO_CAD_HOST` | `0.0.0.0` | FastAPI 监听地址。 |
| `TEXT_TO_CAD_PORT` | `8765` | `run.sh` 启动时的 FastAPI 端口。 |
| `TEXT_TO_CAD_FRONTEND_HOST` | `0.0.0.0` | Viewer 监听地址。 |
| `TEXT_TO_CAD_FRONTEND_PORT` | `5678` | Viewer 开发端口。 |

直接运行 `uv run --python <version> python -m backend` 时，入口默认监听 `127.0.0.1:8000`。使用 `run.sh` 才是配对的 8765/5678 流程。

!!! danger

    应用会在本机执行生成的 Python。当前安全模型不支持把服务绑定到不可信机器或网络。
