# Project 与 Run

## Project

Project 是一次设计对话的持久化边界。其目录包含元数据、Python 源码工作区、对话记录、
预览、诊断信息和已接受的产物版本。Catalog 可以包含多个 Project；每个 Project 有一个
生成的十六进制 `project_id`。

Project 状态为 `Draft`、`Running`、`Succeeded`、`Failed` 和 `Stopped`。一个 Project 同时
只能运行一个 Agent turn。Viewer 要求确认名称后才会删除 Project，删除会移除其本地目录。

## Run 与 conversation turn

Run 是 Agent 执行一条已提交 Prompt 的过程。一个 Project 的对话可以包含多个 turn，后续
消息可以修改已接受模型或重试失败尝试。每个 turn 会记录用户消息、Harness、模型元数据、
工具活动、进度事件、结果，以及提供商报告的有限 token 用量。

当前唯一可选 Harness 是 `deepagents`。它能访问 Project 的 Python 源码和只读仓库 Skill，
但不能访问仓库示例或其他内部运行目录。

## 状态转换

```text
Draft -> Running -> Succeeded
                 -> Failed
                 -> Stopped
Succeeded/Failed/Stopped -> Running（新消息或重试）
```

后端重启时，协调器会把被中断的 `Running` Project 恢复为 stopped。之后可以发送新消息
开启下一次 turn。
