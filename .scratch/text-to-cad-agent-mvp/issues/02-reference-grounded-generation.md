# 02 — 实现 reference-grounded 单轮 CAD 生成

**What to build:** 让一个主 Deep Agent 接收 Project 的完整 Prompt，自主查阅 SimpleCADAPI 依据、完成 Model Source 并执行一次成功路径，不向用户追问，也不依赖跨 Project 记忆或 subagent。

**Blocked by:** 01 — 定义 Agent Run 契约与受限工具面.

**Status:** ready-for-human

- [x] LangChain Deep Agents 是实际生成引擎，并且每个 Agent Run 只使用一个主 Agent、run-local planning、无 subagent、无跨 Project memory。
- [x] Draft Project 接收一次合法 Prompt 后进入 Running；该 Prompt 随即持久化且不可编辑，同一 Project 不能接收第二个 Prompt 或第二次 Agent Run。
- [x] Agent 将 Prompt 视为完整需求，不暂停等待澄清；未指定长度单位时按毫米处理，并自行推断缺失的尺寸或构造细节。
- [x] 关键推断和假设记录在 Model Source 中，使维护者能够从最终源码理解 Agent 如何消解欠明确描述。
- [x] Agent 在编写调用前按工具契约读取 Skill、API/stdlib 索引、所用 API 的精确文档，并在相关时查阅仓库示例，而不是凭空猜测 SDK 接口。
- [x] Agent 可以修改完整的当前 Model Source，包括 imports 和 helper functions，但生成目标保持为一个物理零件、一个入口文件和一个被捕获的最终 Solid。
- [x] 第一次 CAD 执行产生 Validated Result 时，Project 进入 Succeeded，最新 Model Source 和 canonical Scene Artifact 持久化并可通过服务边界取得。
- [x] 第一次 CAD 执行未通过时，Project 能以结构化诊断结束而不误标为 Succeeded，为后续有界修复 ticket 提供稳定接入点。
- [x] Agent 配置只从后端环境取得；Provider、model、endpoint 和 key 不进入浏览器数据或 Agent 生成的 CAD 子进程。

## Comments

- 已接入真实 LangChain Deep Agents 主 Agent、run-local `write_todos` planning、受限 reference-grounded 工具面和一次 CAD 执行；Project Prompt、状态、Model Source、诊断与 canonical Scene Artifact 持久化在当前 Project 目录。
- 工具层额外保证先写入本轮完整 Model Source 再执行，并将 executor 异常也计入本轮执行上限，避免 scaffold 被误判为结果或重复执行。
- 已验证：`uv run pytest`（26 passed）、`uv run --with mypy mypy backend --ignore-missing-imports`、`uv run python -m compileall -q backend tests`、`uv run black --check ...`、`uv run ruff check backend tests`、`uv lock --check`、`cd viewer && npm run build`。
- 已按 `code-review` 以 issue 01 提交 `eaea6fc` 为固定点完成 Standards/Spec 两轴审查，未发现未处理问题。
