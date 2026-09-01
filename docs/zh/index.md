---
icon: lucide/box
---

# CadFlowAgent

CadFlowAgent 是一个面向本机运行、可审计的 CAD Agent 运行时。模型编写可执行的
Python 程序，CadFlow 使用确定性的几何内核执行它，应用则把源码、测量结果、进度
事件和通过验证的产品产物保存在同一个 Project 中。

它不是一步生成网格或图片的黑盒 3D 模型，而是一条可编程、可验证的建模循环。生成
后的 Python 仍然可读、可修改，几何检查也能提供超越视觉相似度的证据。

!!! warning "Alpha 与可信本机软件"

    当前版本面向可信的本机演示。生成的代码会在本机受限进程中执行；它不是托管的
    多租户服务，也不是面向不受信用户的安全边界。

## 当前可用能力

- FastAPI Project/Run API，以及同源的 Three.js Viewer。
- Deep Agents Harness，可在一个 Project 工作区中创建或修改 `/code/model.py` 与辅助模块。
- 支持 CadFlow `Shape` 和语义化 `Assembly`，包含产品验证、Scene 生成以及通过验收前的独立 CAD review。
- 实时源码预览、进度事件、脱敏 Trace 下载，以及已接受结果的版本化产物。
- 五个公开 CAD Skill，覆盖零件、柔性几何、STEP/BREP 检查、装配和旋转传动。

## 从哪里开始

- [快速开始](quickstart.md)：安装锁定环境并启动本机应用。
- [创建第一个 Project](first-project.md)：完成 Project 创建和首次任务提交。
- [示例目录](examples/index.md)：查找可运行的仓库示例。
- [系统架构](architecture.md)：了解 Agent、执行器、内核和 Viewer 的边界。
- [配置参考](reference/configuration.md)：查看有效环境变量与路径。

[GitHub 仓库](https://github.com/zion-zion-zion/CadFlowAgent)包含源码、测试、Skills、示例和 Issue 跟踪器。

## 项目方向

运行时和可观察轨迹已经实现。仓库提供一个小型重建数据示例，但规模化数据集整理、
评测基础设施和 CAD 专用后训练仍属于未来工作，不应理解为已经发布的模型能力。
