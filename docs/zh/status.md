# 项目状态与限制

## 已实现

- 本机 FastAPI 后端和 Vite/Three.js 浏览器工作区。
- 支持多个 Project 的持久化 Catalog，每个 Project 同时只能有一个活动 Run。
- 带只读 Skills 和受限 Python 源码工作区的 Deep Agents Harness。
- `cad.Shape` 单零件和语义化 `cad.Assembly` 产品执行。
- 几何、产品、Scene、STEP 重放和独立 review 检查，检查通过后生成版本化 Accepted 产物。
- 实时 GLB 预览、SSE 进度、Trace 查看、脱敏下载和产品下载。

## 有边界的能力

- 柔性 SDK 与 Assembly API 可以在示例和 Skills 中使用，但并非所有示例都符合 Agent 当前的返回契约。
- 实时预览只用于排查，可能延迟、失败或暂时不可用；Accepted 结果仍以最终验证为准。
- 推理模式和可选 review 模型取决于 OpenAI 兼容端点的配置。
- 应用面向可信本机，生成代码在本机的受限进程中执行。

## 计划中的工作

规模化重建数据集、基准和评测流水线、CAD 专用后训练都还没有实现。轨迹示例不代表已经发布的数据集或训练完成的模型。

## 契约限制

- Part 结果必须是一个有效且体积为正的 solid。
- Assembly 必须保留语义化 Part 边界，不能把独立零件熔成 Shape 来代替 Assembly。
- 一个 Project 不能并发运行两个 Agent turn。
- Prompt 上限为 32,000 个字符。
- 每个 Project 默认保留十个 Accepted 产物版本。
- Shape/Assembly 有效不代表强度、公差、可制造性、热性能或其他未实现的工程分析通过。
