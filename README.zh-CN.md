# CadFlowAgent

CadFlowAgent 是一个面向可信本机的 CAD Agent 运行时：Agent 编写可执行的参数化 CAD
程序，CadFlow 验证几何，并把可观察的运行过程保存为可复用的 Project 证据。它不是一步
输出网格或图片的黑盒生成器，生成后的 Python 仍然可读、可修改。

当前 Alpha 版本提供 FastAPI Project/Run API、Vite/Three.js Viewer、`deepagents` Harness、
`cad.Shape` 单零件和语义化 `cad.Assembly` 产品、确定性验证、独立 CAD review、实时预览
以及版本化产物。规模化数据集和 CAD 专用后训练属于未来方向，并非已经发布的模型能力。

## 文档

- [简体中文文档站](https://zion-zion-zion.github.io/CadFlowAgent/zh/)
- [English documentation site](https://zion-zion-zion.github.io/CadFlowAgent/en/)
- [中文文档源](docs/zh/index.md)
- [English documentation source](docs/en/index.md)

## 快速开始

```bash
./setup.sh
# 在 .env 中填写 OPENAI_MODEL_ID 和 OPENAI_API_KEY
./run.sh
```

Viewer 地址是 `http://localhost:5678`，API 文档地址是 `http://localhost:8765/docs`。平台要求、
配置和第一个 Project 的步骤见[快速开始](docs/zh/quickstart.md)。

## 检查

```bash
# Linux
uv run --locked --python 3.12 pytest
# macOS
uv run --locked --python 3.13 pytest
cd viewer && npm run build
cd .. && ./scripts/docs.sh build
```

CadFlowAgent 使用 [GNU Affero General Public License v3.0](LICENSE) 授权。
