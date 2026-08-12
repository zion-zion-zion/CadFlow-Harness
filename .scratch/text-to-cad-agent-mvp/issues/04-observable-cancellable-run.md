# 04 — 交付可观察、可停止的 Agent Run

**What to build:** 让运行中的 Agent Run 具有产品级可观察性和确定的生命周期控制：用户能看到经过筛选且可恢复的进度，能真正停止 Agent 与 CAD 子进程，并且多个 Project 不会争用本机资源。

**Blocked by:** 03 — 实现有界执行—诊断—修复循环.

**Status:** ready-for-human

- [x] Agent Run 持久化并通过 SSE 发布 Generation Stage、工具类别、CAD 尝试次数和短结果等 curated Progress Events。
- [x] Progress Event 使用单调递增 ID，连接包含 keepalive，并能根据 `Last-Event-ID` 重放遗漏事件；刷新或切回 Project 后时间线仍然连贯。
- [x] SSE 不包含自然语言模型流、chain of thought、模型 token、完整工具参数、原始 stdout/stderr、完整进程日志或 Provider 凭据。
- [x] 单服务进程内全局最多有一个 Agent Run；另一个 Draft Project 的启动请求得到确定的冲突响应，而查看或切换 Project 不会中断正在运行的任务。
- [x] Stop 只对 Running Project 有效，并同时取消 Deep Agent task、修复循环和当前 CAD 子进程，使 Project 最终进入 Stopped。
- [x] Stopped Project 保留 Prompt、最新 Model Source、Progress Events 和诊断，但不提供未验证 Scene Artifact，也不能再次运行。
- [x] 活动 Agent task、CAD process handle、取消控制和全局运行锁只保存在单个服务进程内，不引入数据库、外部队列或多 worker 协调。
- [x] 服务启动时能够从磁盘重建 Project 状态；遗留的 Running Project 转为带简短原因的 Failed，而不是假装恢复已经丢失的 Agent checkpoint。
- [x] 完成态 Project 的事件、源码、诊断和 Validated Result 在服务重启后仍可访问，且不存在遗留的活动锁或孤立进程声明。

## Comments

- 新增 `ProgressEventStore`，将 curated Progress Event 持久化到 Project 的 `events.jsonl`；事件 ID 按 Project 单调递增，FastAPI `/events` 提供 SSE keepalive、`Last-Event-ID` 重放和测试用的有限回放模式。
- `AgentRunCoordinator` 在单个服务进程内持有全局运行槽、活动 Agent task 和取消控制；`CancellationToken` 会立即绑定并终止当前 CAD 子进程，Stop 收尾为 `Stopped` 并清除未验证 artifacts。
- `ProjectStore` 增加 Stopped 收尾和启动恢复：磁盘中的遗留 `Running` Project 变为 `Failed`，完成态的源码、事件、诊断和 canonical Scene Artifact 继续由磁盘提供。
- 新增 FastAPI Project/Run/Stop/Events/Scene 边界及确定的全局冲突响应；Progress Event 只保留阶段、工具类别、尝试次数和短结果，不暴露模型流、完整工具参数、原始日志或凭据。
- 已验证：`uv run pytest`（40 passed）、`uv run --with mypy mypy backend --ignore-missing-imports`、`uv run ruff check backend tests`、`uv run black --check`、`uv lock --check`、`cd viewer && npm run build`。
- 已按 `code-review` 的 Standards/Spec 两轴以固定点 `71e0afd` 审查 staged diff；未发现未处理的规范或 issue 04 验收缺口。
