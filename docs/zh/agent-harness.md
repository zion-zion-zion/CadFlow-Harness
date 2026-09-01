# Agent Harness

当前运行时只暴露一个 Harness 标识符：`deepagents`。模型服务通过 OpenAI 兼容环境变量
配置，后端使用 LangChain 的 `ChatOpenAI` 集成构造模型。

## 文件系统契约

一次 Run 中，Agent 看到两个虚拟根目录：

- `/code/` 是当前 Project 可写的 Python 源码目录。必须有 `model.py`，可以添加辅助模块。
- `/skills/` 是只读仓库 Skill 参考目录。

Agent 不能通过文件工具修改 Skill、浏览其他 Project 或读取仓库示例。源码验证后由执行器
生成运行时产物，源码本身不应直接写这些产物。

## 工具边界

Harness 获得有边界的文件和验证工具，可以查看源码、写入或编辑 Python、请求模型验证，
并依据结构化证据修复失败。当前运行时不向 Agent 暴露任意 Shell 执行和子 Agent 任务委派。

## 模型设置

`OPENAI_MODEL_ID` 和 `OPENAI_API_KEY` 必填。`OPENAI_BASE_URL` 用来选择 OpenAI 兼容端点。
推理模式为 `none` 时使用 Chat Completions；未设置推理级别或设置其他级别时使用 Responses
API。可选的 `OPENAI_REVIEW_MODEL_ID` 用来指定 review 模型。

单次 Run 默认墙钟预算为 1,200 秒，由 `CADFLOW_AGENT_RUN_TIMEOUT_SECONDS` 控制。Run 仍
可能因为提供商、Python 源码、CadFlow 操作或验证门失败而提前结束。
