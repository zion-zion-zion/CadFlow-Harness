---
icon: lucide/box
---

# CadFlow Harness

CadFlow Harness 是一个在本机运行 CAD Agent 的工具。Agent 编写可执行的 Python 程序，CadFlow 用确定性的几何内核运行程序，应用会保存源码、测量值、进度事件和通过检查的产品产物。

生成结果保留为 Python 源码。你可以查看尺寸和特征，修改文件后再次运行。Viewer 用于查看预览和已接受的几何结果。

!!! warning "Alpha，仅适合可信本机"

    当前版本不是托管的多租户服务，也不是隔离不可信代码的安全边界。生成的代码会在本机受限进程中执行，请只在你信任的机器上运行。

## 当前可用

- FastAPI Project 和 Run API，以及同源的 Three.js Viewer。
- Deep Agents Harness，可在一个 Project 中创建或修改 `/code/model.py` 和辅助模块。
- `Shape` 单零件与语义化 `Assembly` 执行、产品验证、Scene 生成和独立 CAD review。
- 实时源码预览、进度事件、脱敏 Trace 下载，以及已接受结果的版本化产物。
- 五个公开 CAD Skill，覆盖零件、柔性几何、STEP/BREP、装配和旋转传动。

## 从这里开始

- [快速开始](quickstart.md)：安装锁定的环境并启动本地应用。
- [创建第一个 Project](first-project.md)：创建 Project，提交第一个任务。
- [示例目录](examples/index.md)：查找仓库中的可运行示例。
- [系统架构](architecture.md)：了解 Agent、执行器、几何内核和 Viewer 的分工。
- [配置](reference/configuration.md)：查看环境变量和目录。

[GitHub 仓库](https://github.com/zion-zion-zion/CadFlowAgent)包含源码、测试、Skills、示例和 Issue 列表。

## 项目状态

运行时和运行记录已经在仓库中实现。仓库里有一个小型重建数据示例；大规模数据集整理、评测基础设施和 CAD 专用后训练尚未实现，也不属于当前发布的模型能力。
