# 05 — 集成本地 Project 工作区

**What to build:** 将已完成的 Agent Run 能力包装成可信本机、单用户、单轮的 Text-to-CAD Demo，使用户能管理 Project、提交 Prompt、查看进度、控制运行并直接检查 Validated Result。

**Blocked by:** 04 — 交付可观察、可停止的 Agent Run.

**Status:** ready-for-agent

- [ ] 用户可以创建带 opaque Project ID 的 Draft Project；名称可读、允许重复，Project 目录不会由名称或其中的路径字符决定。
- [ ] Project Catalog 从文件系统数据构建、按最近活动排序并在刷新后保留，用户可以切换 Project 而不影响其他 Project 的 Agent Run。
- [ ] Prompt 输入拒绝空值和超过约定上限的内容，支持 Cmd/Ctrl+Enter 提交，并在提交后显示为只读。
- [ ] 页面采用桌面三栏结构：Project Catalog、当前 Project 的 Prompt/进度/控制区，以及 CAD Viewer；Running 时提供 Stop，其他 Draft 在全局占用期间不能启动。
- [ ] Succeeded Project 自动从服务取得自己的 canonical Scene Artifact；Draft、Running、Failed 和 Stopped 分别显示自己的 Viewer 空状态，不会残留另一个 Project 的几何体。
- [ ] Viewer 保留 Scene ZIP 完整性检查、GLB 渲染、旋转、平移、缩放、自动取景和 Fit，移除模型树、Inspector、实体选择、源码面板、CodeMirror、本地 ZIP 上传和无关控制。
- [ ] Scene Artifact 只对 Succeeded Project 可取得；失败、停止和部分输出不能通过 Viewer 路径伪装成 Validated Result。
- [ ] 永久删除要求用户输入所选 Project 的名称确认；删除 Running Project 时先取消 Agent Run 和子进程，再移除 Project，并立即从 Catalog 消失。
- [ ] 服务使用单个 Uvicorn worker、默认绑定 `127.0.0.1`，生产前端由 FastAPI same-origin 提供，开发环境使用 API proxy，不启用 CORS。
- [ ] 浏览器没有 Provider 配置入口或凭据；Demo 不引入认证、数据库、任务队列、容器 sandbox、公开或 LAN 部署路径。
