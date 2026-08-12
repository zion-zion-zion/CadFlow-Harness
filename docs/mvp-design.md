# Text-to-CAD Agent MVP 设计

状态：已确认，尚未开始实现。

## 1. 目标

构建一个本机单用户 Text-to-CAD Demo。用户创建 Project，提交一次完整的纯文本 Prompt，Deep Agent 自动查阅 CadFlow Skill、编写和执行 Python、根据错误进行有限修复，最终生成可保留的 Model Source 和供 Viewer 加载的 Scene Artifact。全过程不要求用户补充信息，也不构成多轮对话。

MVP 只支持一个物理零件，最终结果必须是一个有效 Solid。

## 2. 不在范围内

- 多轮对话、Prompt 修改和同一 Project 重试。
- 多零件装配、多文件生成和 Project 本地模块导入；后两项是未来复杂模型的扩展方向。
- 图片、草图、STEP 或其他附件输入。
- STEP、STL 或其他制造格式输出。
- 源码查看、编辑或下载界面；Model Source 只保存在 Project 目录。
- 手机布局、认证、数据库、外部任务队列、多 Worker 和公网部署。
- 视觉模型或语义模型对 CAD 外观进行二次评分。

领域术语以根目录的 `CONTEXT.md` 为准。

## 3. 用户流程

1. 页面启动后从文件系统加载 Project Catalog，按最近更新时间倒序排列，并选中 URL 中的 Project；没有 URL 选择时选中最近更新的 Project。
2. 用户点击“新建项目”，输入必填的显示名称。名称允许重复，后端生成唯一 Project ID，并立即创建持久化的 Draft Project。
3. 用户在当前 Draft Project 的多行输入框中输入一次纯文本 Prompt，通过按钮或 `Cmd/Ctrl+Enter` 提交。空 Prompt 和超过后端上限的 Prompt 不可提交。
4. Prompt 提交后永久只读，Project 进入 Running。全局同时只能存在一个 Agent Run；其他 Draft Project 暂时不能开始运行。
5. 页面通过 SSE 显示查阅文档、编写模型、执行、修复和验证等 Progress Event。用户可以切换到其他 Project，后台运行不受影响。
6. 用户可以随时点击“终止”。按钮无需确认，点击后进入不可重复点击的“正在终止”状态；后端取消 Agent 和 CAD 子进程，Project 最终成为 Stopped。
7. Agent Run 成功后，当前 Project 的 Scene Artifact 自动加载到右侧 Viewer。Failed、Stopped 和 Draft Project 显示各自的空状态，不沿用其他 Project 的模型。
8. 删除操作需要包含项目名称的确认。确认后，后端先终止可能仍在运行的任务，再永久删除整个 Project 目录；该 Project 立即从 Catalog 消失。

Project 不自动过期或清理，允许同时存在多个 Draft Project。

## 4. Project 状态机

```text
Draft -> Running -> Succeeded
                 -> Failed
                 -> Stopped
```

- 同一 Project 最多接收一个 Prompt 和一个 Agent Run。
- Failed 和 Stopped 都不能重新运行。
- 删除不是状态；它会移除 Project。
- 服务启动时发现遗留的 Running Project，会将其标记为 Failed，不尝试恢复 Agent。

## 5. 总体架构

### 5.1 后端

- FastAPI，单个 Uvicorn worker。
- 默认监听 `127.0.0.1`。
- 生产模式由 FastAPI 同源托管 Viewer 构建产物；开发模式由 Vite 将 `/api` 代理到 FastAPI。
- 不启用 CORS。
- Deep Agent 在后台异步任务中运行。
- CAD 程序由独立、可终止的 Python 子进程执行。
- 进程内保存活动任务句柄和全局单任务锁；持久状态全部写入 Project 目录。
- 不使用数据库、Redis、Celery 或外部队列。

### 5.2 前端

继续使用原生 TypeScript、Vite 和 Three.js，不引入 React 或 Vue。桌面端使用三栏布局：

```text
Project Catalog | 当前 Project 的 Prompt、事件与控制 | CAD Viewer
```

Viewer 从现有实现中保留：

- `.scene.zip` 解包与完整性校验。
- GLB 和 Scene Manifest 加载。
- Three.js 渲染、旋转、缩放、平移、自动适配和 Fit。

删除模型树、Inspector、实体选择、源码 Dock、CodeMirror、本地 ZIP 上传和其他非结果展示功能。MVP 不专门适配手机。

## 6. Project 存储

```text
output/projects/<project_id>/
├── project.json
├── prompt.txt
├── model.py
├── events.jsonl
├── logs/
│   ├── attempt-1.stdout.log
│   ├── attempt-1.stderr.log
│   └── ...
└── artifacts/
    └── model.scene.zip
```

- `project.json` 是 Project 状态和时间戳的持久记录。
- `events.jsonl` 保存经过整理、可按 ID 重放的 Progress Event。
- 日志按执行次数保存，设置大小上限，并对疑似凭证文本做脱敏。
- 只保留最新 `model.py`，不保存每轮源码快照，也不创建 Git 提交。
- Scene Artifact 只有通过全部验收后才能作为成功结果提供给 Viewer。

## 7. Deep Agent

### 7.1 模型配置

后端只支持一套由环境变量配置的 OpenAI-compatible 模型，不在页面提供 Provider、模型或密钥选择。启动时缺少必要配置应给出明确错误。

### 7.2 Agent 结构

- 必须使用 LangChain Deep Agents。
- 只使用一个主 Agent，不创建子 Agent，不保留跨 Project 记忆。
- 可以使用 Deep Agents 的本次运行规划能力。
- Agent 的自然语言最终回答不进入产品界面；页面只显示整理后的 Progress Event 和最终状态。

### 7.3 文档与工具

后端通过 Deep Agents 的 SkillsMiddleware 加载 `skills/cadflow-model-part/`。Agent 使用
内置文件工具按需阅读 Skill、API 文档和 examples；后端不再追踪阅读顺序，也不要求
Agent 声明使用了哪些 API。

Agent 获得以下能力：

- 按需读取 Skill、API 文档和 examples。
- 读取和修改当前 Project 的 Model Source。
- 调用唯一的零参数 `validate_model` 工具。
- 使用 Deep Agents 的通用文件工具进行目录浏览、检索、读取和编辑。
- 使用本地 Shell 运行命令、Python 诊断脚本，以及环境中已安装的依赖和辅助工具。

通用工具由 `LocalShellBackend` 提供，以当前 Project 为默认工作目录，并继承后端进程环境。
每次运行的 System Prompt 会注入当前 Project 的绝对路径，并要求 Agent 仅在该目录内
读取和写入 Project 文件；仓库的 `skills/` 与 `examples/` 作为只读例外，可供 Agent
浏览、检索和读取。Agent 不应扫描父目录、其他 Project 或无关路径。
它不提供沙箱隔离；本项目按可信本机开发环境使用。正式 CAD 结果仍必须通过
`validate_model` 的结构化验证。它不检查文档阅读、API 名称、源码内容或编辑方式，
只执行当前 `model.py` 并返回验证事实。

### 7.4 Model Source

后端先创建 `model.py` 骨架，固定以下约定：

- 使用当前 Project 的 `artifacts/` 作为 `export_dir`。
- 只有一个 `build_model(model: cad.Model) -> cad.Shape` 顶层入口。
- 捕获一个最终 Solid。
- 产出 canonical `model.scene.zip`。

Agent 可以编辑整个文件，以便增加导入、辅助函数和本地模块，也可以在需要时使用或安装其他依赖。建模 API 以 CadFlow 文档化的 `Model`/`Shape` 为准；后端不做 AST、import 或危险调用检查。后端验证 native Shape 后，通过 CadFlow 的公开 STEP/Scene 接口生成 canonical Scene Artifact。

未来支持复杂装配时，可以在同一 Project 中生成多个本地 Python 模块并互相导入，但仍不默认允许任意第三方依赖。

Prompt 未给出单位时默认使用毫米；缺少尺寸或结构细节时由 Agent 选择合理值，并在 `model.py` 顶部注释中记录关键假设，不向用户追问。

### 7.5 执行与修复

- 最多执行三次，包括第一次执行和修复后的重试。
- 整个 Agent Run 最长五分钟。
- 单次 CAD Python 执行最长 120 秒。
- 可重试的模型 API 错误最多额外重试两次，不占 CAD 执行次数，但仍受五分钟总时限限制。
- CAD 子进程使用 FastAPI 当前的 Python 解释器，以 Project 为工作目录，并从环境中移除模型 API key 等无关凭证。
- 不进行容器隔离或生成源码静态安全检查。

`validate_model` 返回结构化结果，包括退出码、截断错误、最终 Solid 数量和体积、Scene ZIP 是否存在及能否解析。Agent 根据结果决定是否修复；三次仍未通过则 Project 成为 Failed。

## 8. 成功条件

以下条件必须全部满足：

1. Python 子进程退出码为 0。
2. 捕获结果恰好是一个 Solid。
3. Solid 体积有限且大于 0。
4. 只存在预期的 `artifacts/model.scene.zip`。
5. Scene ZIP 能通过现有 Viewer 的包结构、成员和哈希检查并成功解析。

MVP 不自动判断结果在语义或视觉上是否准确匹配 Prompt。

## 9. Progress Event 与 SSE

Progress Event 只包含整理后的阶段、工具名称、执行次数和简短结果，不包含：

- LLM token 或自然语言回答流。
- Chain of Thought。
- 完整工具参数。
- 完整 stdout/stderr。
- 模型 API 配置和密钥。

事件阶段包括：准备项目、查阅文档、编写模型、第 n/3 次执行、修复模型、验证产物、完成、失败和终止。

事件写入 `events.jsonl` 并拥有递增 ID。SSE 支持基于事件 ID 的重放，使页面切换、刷新或临时断线后能够恢复时间线。

## 10. API 契约

```text
GET    /api/projects
POST   /api/projects
GET    /api/projects/{project_id}
DELETE /api/projects/{project_id}
POST   /api/projects/{project_id}/run
POST   /api/projects/{project_id}/stop
GET    /api/projects/{project_id}/events
GET    /api/projects/{project_id}/scene
```

- `POST /api/projects` 创建 Draft Project。
- `POST /run` 只接受 Draft Project，并在另一个 Agent Run 活动时拒绝启动。
- `POST /stop` 只操作 Running Project。
- `/events` 返回 `text/event-stream`，支持重放和 keepalive。
- `/scene` 只对 Succeeded Project 返回 canonical Scene ZIP。
- 不提供 Model Source 的页面查看或下载接口。

## 11. 测试与验收

- 纯存储、状态机、Scene 校验等单元测试不调用模型。
- 所有涉及 Agent 生成的集成和端到端测试必须使用真实模型，不使用 Fake Agent。
- 真实 Agent 测试使用 `live_agent` marker，默认 `pytest` 不执行；显式运行 `pytest -m live_agent` 才产生真实模型请求。
- 后端普通测试运行 `uv run pytest`。
- Viewer 至少通过 `npm run build`。
- 最终真实模型冒烟测试使用固定 Prompt：

> 创建一个外径 80 mm、厚 10 mm、中心孔直径 30 mm、节圆直径 60 mm、均布 6 个直径 6 mm 通孔的圆形法兰盘，所有边缘做 1 mm 倒角。

冒烟测试必须走完整的页面或 API 链路并产生可交互渲染的 Scene Artifact。

## 12. 已知安全限制

- Agent 生成的 Python 会直接执行，没有容器、AST 检查或系统调用隔离。
- 服务只能作为可信本机 Demo 使用，不能监听不可信网络。
