# 01 — 定义 Agent Run 契约与受限工具面

**What to build:** 建立可信本机 Demo 中 Agent Run 的最小执行边界，使 Agent 能从完整的 Model Source 骨架开始，只在当前 Project 范围内查阅依据、修改源码并执行模型，同时让每次执行产生足以支持验证和后续修复的结构化结果。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] 每次 Agent Run 开始前都会生成完整的单零件 Model Source 骨架，明确一个模型入口、最终 Solid 捕获方式、Project artifact 目录和 canonical Scene Artifact 输出要求。
- [ ] Agent 只能读取打包的 SimpleCADAPI Skill、API/stdlib 索引、精确 API 文档和仓库示例，只能读写当前 Project 的 Model Source，并且没有通用 Shell 或跨 Project 文件工具。
- [ ] 工具契约要求 Agent 先读 Skill 入口和规定索引，再读取其实际采用的每个 API 的精确文档；这些要求能从 Agent Run 的工具使用记录中验证。
- [ ] Model Source 使用服务自身的 Python 解释器和 Project 工作目录执行，不做 AST、import 或危险调用静态检查；允许依赖仍是 Agent 指令，不被描述成安全隔离。
- [ ] CAD 子进程环境不包含模型 Provider 的 key、endpoint 等凭据，stdout/stderr 有大小上限并对 credential-like 内容脱敏。
- [ ] 单次 CAD 执行最多运行 120 秒，并支持外部取消后终止仍在运行的子进程。
- [ ] `execute_model` 的可观察结果包含执行状态、截断后的错误信息、捕获的 Solid 数量、体积、canonical Scene Artifact 是否存在以及 Scene 解析结果。
- [ ] Scene Artifact 验证与现有 Viewer 的 ZIP 成员、manifest、hash 和可加载性约束一致，不另建宽松的替代协议。
- [ ] 不调用模型即可验证成功、非零退出、超时、输出截断、凭据脱敏和无效 Scene Artifact 等执行结果。
