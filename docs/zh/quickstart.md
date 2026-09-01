# 快速开始

本页说明如何从干净检出启动可信本机演示。

## 环境要求

- Linux x86_64、glibc 2.31 或更高版本、Python 3.12，或 macOS 26 arm64、Python 3.13。
- `setup.sh` 需要安装 `uv` 或 Node.js 时，系统应有 `curl` 或 `wget`。
- Node.js 20.19+、22.12+ 或更高主版本，以及 `npm`。
- OpenAI 兼容模型端点、模型标识符和 API Key。

匹配平台的 CadFlow wheel 已放在 `vendor/`，由 `pyproject.toml` 中的平台标记选择。其他
操作系统和 Python 版本不在内置 wheel 的支持范围内。

## 安装

在仓库根目录运行：

```bash
./setup.sh
```

脚本会检测平台，必要时安装工具，执行 `uv sync --locked --group dev`，用 `npm ci` 安装
`viewer/`，并在不覆盖已有文件的情况下从 `.env.example` 创建 `.env`。只检查现有环境而
不修改文件时运行 `./setup.sh --check`。

在 `.env` 中填写模型服务的必需配置：

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=<model-id>
OPENAI_API_KEY=<api-key>
```

`OPENAI_BASE_URL` 留空时使用提供商默认地址。推理和 review 选项见[配置](reference/configuration.md)。

## 启动应用

```bash
./run.sh
```

脚本会在 `http://localhost:8765` 启动 FastAPI 后端，在 `http://localhost:5678` 启动 Vite
Viewer。打开 Viewer 地址即可使用。生产环境存在 `viewer/dist` 时由后端同源托管；本地
开发时 Vite 会把 `/api` 代理到后端。

如需更换监听地址或端口，可在运行脚本前设置 `TEXT_TO_CAD_HOST`、`TEXT_TO_CAD_PORT`、
`TEXT_TO_CAD_FRONTEND_HOST` 或 `TEXT_TO_CAD_FRONTEND_PORT`。请只在可信网络中开放服务。

## 首次运行清单

1. 在 Project Catalog 中创建一个命名 Project。
2. 输入完整的零件或产品需求并提交。
3. 在 Agent 编辑和验证源码时，观察对话和 Run Progress 面板。
4. 有可用的实时预览时先检查它；Run 成功后再查看已接受的 Scene 和产品标签页。

API 方式见[创建第一个 Project](first-project.md)，不启动模型服务的构建检查见[命令](reference/commands.md)。

## 常见启动问题

| 现象 | 检查 |
| --- | --- |
| 找不到 `uv` 或 Node.js | 运行 `./setup.sh`，或安装工具后运行 `./setup.sh --check`。 |
| 平台或 Python 不受支持 | 使用 Linux x86_64 + Python 3.12，或 macOS 26 arm64 + Python 3.13。 |
| 端口已被占用 | 修改对应的 `TEXT_TO_CAD_*_PORT` 后重试。 |
| 模型启动前 Run 失败 | 确认 `.env` 中有 `OPENAI_API_KEY` 和 `OPENAI_MODEL_ID`，查看 Viewer 错误与后端日志。 |
| 没有预览 | 预览是尽力而为的功能。等待最终验证或直接查看已接受 Scene，并检查预览诊断面板中的错误。 |
