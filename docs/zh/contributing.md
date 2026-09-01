# 参与贡献

贡献应保持运行时契约、公开 API 和生成产物的真实性。工程约定见仓库的
[AGENTS.md](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/AGENTS.md)；其中的内部
Issue 和 Triage 说明不属于公开站点。

## 开发环境

```bash
./setup.sh
uv sync --locked --group dev --python 3.12  # Linux
uv sync --locked --group dev --python 3.13  # macOS
cd viewer && npm ci
```

使用 `setup.sh` 选择的平台 Python。不要提交 `.env`、生成的 `output/`、`examples/out/`、
`viewer/dist/` 或凭证。

## Pull Request 前检查

```bash
uv run --locked --python 3.12 pytest
cd viewer && npm run build
cd .. && ./scripts/docs.sh build
```

后端行为变化应添加聚焦的回归测试。Viewer 变化至少要完成生产构建；若有视觉变化，还应
在 PR 中写明手动检查。文档变化必须以严格模式构建两种语言。

## 文档变更

保持 `docs/en/` 与 `docs/zh/` 的页面范围和事实对应。只链接今天确实存在的源码或 API，
为实验性示例标注状态，并同步更新导航。公开 `docs/` 只面向用户和外部贡献者；内部 Agent
操作规则位于 `.agents/`。

## Pull Request

说明用户问题、解决方案和验证命令。Viewer 变化附上简短截图说明，并明确依赖或配置变化。
文档工作流会在 Pull Request 中严格构建中英文站点，并在 `master` 更新后发布 Pages。
