# 06 — 验证完整 Text-to-CAD MVP

**What to build:** 为完整 Demo 建立可信的自动化和人工验证，使普通测试保持确定、快速和无模型费用，同时用显式 opt-in 的真实模型 smoke test 验证自主 Text-to-CAD 主张。

**Blocked by:** 05 — 集成本地 Project 工作区.

**Status:** ready-for-agent

- [ ] 默认 Python 测试通过 FastAPI 应用边界覆盖 Project 创建、Prompt 提交、状态转换、事件重放、Stop、删除、Scene 获取和重启恢复。
- [ ] 专用执行契约测试覆盖成功、非零退出、超时、进程终止、Solid 数量、有限正体积、预期 Scene Artifact 数量和 Scene 解析结果。
- [ ] Project metadata、状态转换、文件持久化、事件重放、日志脱敏和 Scene validation 的确定性行为可在不调用模型的情况下测试。
- [ ] 任何声称测试 Agent generation、repair 或 cancellation 的测试都使用真实配置模型，不用 Fake Agent 替代自主行为。
- [ ] 真实模型测试使用 `live_agent` 标记并默认排除；缺少显式运行请求或有效 Provider 环境时，普通测试不会产生模型调用或费用。
- [ ] live smoke 使用固定 Prompt：“创建一个外径 80 mm、厚 10 mm、中心孔直径 30 mm、节圆直径 60 mm、均布 6 个直径 6 mm 通孔的圆形法兰盘，所有边缘做 1 mm 倒角。”
- [ ] live smoke 穿过完整服务边界，最终观察到 Succeeded、取得 canonical Scene Artifact 并通过 Viewer 加载路径；不断言 token 序列、精确工具调用顺序或 Agent 内部状态。
- [ ] 取消验证针对真实活动 Agent Run，并观察到 Stopped 以及 CAD 子进程终止；删除验证同时观察 Catalog 和 Project 文件系统数据消失。
- [ ] 普通 Python 测试、显式 live Agent 测试和 Viewer type-check/build 都有清晰的运行命令，且默认测试选择不会意外包含 live marker。
- [ ] 人工验证记录三栏布局、Project 切换、Progress Event 恢复、Stop/删除控制以及 Viewer 的 rotate、pan、zoom、auto-frame 和 Fit 行为。
