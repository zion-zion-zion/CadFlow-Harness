# 03 — 实现有界执行—诊断—修复循环

**What to build:** 在单轮生成成功路径上加入有限的自动修复，使 Agent 能根据真实执行结果改写最新 Model Source，同时对执行次数、总耗时、模型服务重试和最终结果设定明确上限。

**Blocked by:** 02 — 实现 reference-grounded 单轮 CAD 生成.

**Status:** ready-for-human

- [x] Agent 每次 CAD 执行后都收到结构化的退出、错误、Solid、体积和 Scene 解析结果，并以这些事实决定成功、修复或失败。
- [x] 一个 Agent Run 最多执行 CAD 三次，达到上限后必然结束，不会通过重新规划或重新调用工具绕过计数。
- [x] 一个 Agent Run 的总墙钟时间最多五分钟，单次 CAD 执行仍受独立的 120 秒限制。
- [x] 明确可判定为瞬时的模型 Provider 错误最多单独重试两次，且不会消耗 CAD 执行次数；非瞬时错误不会无限重试。
- [x] 修复可以改写整个当前 Model Source，每次覆盖上一个版本，Project 最终只保留最新源码而不形成源码历史系统。
- [x] 只有进程成功退出、恰好捕获一个有限正体积 Solid、仅有预期 canonical Scene Artifact 且 Scene 可解析时，Agent Run 才进入 Succeeded。
- [x] 三次执行或五分钟内仍未得到 Validated Result 时，Project 进入 Failed，并向用户提供简短、可理解且不含 traceback 的失败原因。
- [x] 每次执行的有界、脱敏 stdout/stderr 和结构化诊断保留在 Project 中，供本机维护者排查，但不会作为 Progress Event 或浏览器原始日志暴露。
- [x] Failed Project 不能再次运行，任何未验证或部分生成的 Scene Artifact 都不会被提升或提供为结果。

## Comments

- 新增单一 Deep Agent 的有界 repair loop：最多三次 CAD 执行、五分钟 Agent Run deadline；每次执行将完整结构化 `ExecutionResult` 返回给 Agent，修复直接覆盖当前 `model.py`，不保存源码历史。
- `ChatOpenAI` 配置最多两次 provider retry；CAD executor 仍按剩余 Agent Run 时间和独立 120 秒上限执行。每次执行的脱敏 stdout/stderr、Scene 解析和几何诊断聚合写入 Project `diagnostics.json`。
- Failed Project 收尾时清理未验证 artifact，保留 Prompt、最新 Model Source 和诊断；Succeeded 仍只通过既有 canonical Scene Artifact 边界提供结果。
- 已验证：`uv run pytest`（33 passed）、`uv run --with mypy mypy backend --ignore-missing-imports`、`uv run python -m compileall -q backend tests`、`uv lock --check`、`cd viewer && npm run build`、`uv run ruff check ...`。
- 已按 `code-review` 的 Standards/Spec 两轴审查，以 issue 02 提交 `333a464` 为固定点，未发现未处理问题。
