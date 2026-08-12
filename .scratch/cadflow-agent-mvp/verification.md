# Text-to-CAD MVP 验证记录

## 自动化命令

在仓库根目录执行：

```bash
uv run pytest
uv run pytest -o addopts='' -m live_agent
uv run --with mypy mypy backend --ignore-missing-imports
cd viewer && npm run build
```

普通 `uv run pytest` 通过 `pyproject.toml` 的默认 marker 选择排除 `live_agent`，不会调用模型。第二条命令只有在显式设置 `TEXT_TO_CAD_RUN_LIVE_AGENT=1` 且 `OPENAI_API_KEY`、`OPENAI_MODEL_ID` 有效时才会发起真实 Provider 请求：

```bash
TEXT_TO_CAD_RUN_LIVE_AGENT=1 uv run pytest -o addopts='' -m live_agent
```

未满足显式 opt-in 或 Provider 配置时，live 测试直接 skip，不产生模型调用。`live_agent` smoke 使用 issue 06 规定的固定法兰盘 Prompt；取消测试使用真实 Agent 生成的 Model Source，并等待真实 CAD 子进程进入活动态后再调用 Stop。

本次确定性验证记录（2026-08-12）：`uv run pytest -q` 为 `50 passed, 2 deselected`；未设置 opt-in 时显式运行 live 选择为 `2 skipped`；mypy、`compileall` 和 Viewer `npm run build` 均通过。Viewer build 仍有 Vite 的 bundle size warning，不影响 type-check/build 退出状态。

## 人工验证记录

启动本地 Demo 后，按下面顺序记录浏览器观察结果。该记录用于 human review，不用 token、内部 tool-call 顺序或原始日志作为验收依据。

| 检查项 | 操作 | 预期观察 |
| --- | --- | --- |
| 三栏布局 | 打开首页并创建两个 Project | 左侧 Project Catalog、中间 Prompt/Progress/Control、右侧 CAD Viewer 同时可见；桌面宽度下无横向滚动遮挡 |
| Project 切换 | 在一个 Project 运行时点击另一个 Draft，再切回运行中的 Project | 切换只改变当前显示 Project；原 Project 的 Progress Event 时间线连续，运行没有被打断 |
| Progress Event 恢复 | 刷新页面，或先切换 Project 再切回 | 已有事件按顺序恢复，新的事件接在最后；页面不显示 token、chain of thought、完整参数或原始 stdout/stderr |
| Stop 控制 | 运行中的 Project 点击 Stop | 控件只在 Running 显示；状态变为 Stopped，未验证 Scene 不出现在 Viewer 中 |
| 删除控制 | 点击 Delete，先输入错误名称，再输入当前 Project 名称确认；重复时选择一个 Running Project 删除 | 错误确认不会删除；正确确认会从 Catalog 消失，Running 删除先停止运行再移除数据 |
| Viewer rotate / pan / zoom | 在 Succeeded Project 的模型上拖拽旋转、按住平移、滚轮缩放 | 模型可交互观察，操作不会改变 Project 状态 |
| Viewer auto-frame / Fit | 加载 Scene，观察自动取景，再执行 Fit | 加载后自动居中并完整显示模型；Fit 后恢复完整取景 |

人工结果：`待 human review`。浏览器交互是 issue 06 交付后的最后人工门槛；自动化测试已覆盖 HTTP、Scene Artifact、状态和事件契约。
