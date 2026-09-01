# 命令

除非特别说明，命令都从仓库根目录执行。

## 应用

```bash
./setup.sh
./setup.sh --check
./run.sh
```

`setup.sh` 安装或检查平台依赖。`run.sh` 同时启动后端和 Viewer，退出时清理两个子进程。

## 文档

使用以下命令安装包含 Zensical `0.0.57` 的锁定开发依赖：

```bash
uv sync --locked --group dev --python 3.12  # Linux
uv sync --locked --group dev --python 3.13  # macOS
```

以严格模式构建两种语言并重建 `site/`：

```bash
./scripts/docs.sh build
```

输出是可部署的静态目录，包含 `/index.html`、`/en/` 和 `/zh/`。根路径会跳转到中文站点。清理命令为：

```bash
./scripts/docs.sh clean
```

先构建再启动合并站点的本地预览：

```bash
./scripts/docs.sh serve
```

预览默认监听 `http://127.0.0.1:8000`，可通过 `DOCS_HOST` 或 `DOCS_PORT` 修改。`zensical serve` 只能预览单种语言；合并站点使用仓库脚本。

## 验证

```bash
# Linux
uv run --locked --python 3.12 pytest
# macOS
uv run --locked --python 3.13 pytest
cd viewer && npm ci && npm run build
```

真实模型服务测试需显式使用 `-m live_agent`。
