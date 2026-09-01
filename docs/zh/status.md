# 项目状态与限制

## 已实现

- 可信本机 FastAPI 后端和 Vite/Three.js 浏览器工作区。
- 支持多个 Project 的持久化 Catalog，以及每个 Project 同时一个活动 Run。
- 带只读 Skills 和受限 Python 源码工作区的 Deep Agents Harness。
- `cad.Shape` 单零件和语义化 `cad.Assembly` 产品执行。
- 确定性的几何、产品、Scene、STEP 重放和独立 review 检查，之后生成版本化 Accepted 产物。
- 实时 GLB 预览、SSE 进度、有限 Trace、脱敏和产品下载。

## 实验性或有边界的能力

- 柔性 SDK 与 Assembly API 可通过示例和 Skills 使用，但并非每个示例都符合 Agent 当前
  的返回契约。
- 实时预览是尽力而为的能力，可能滞后、失败或暂时不可用；这不影响有效的 Accepted 结果。
- 提供商推理模式和可选 review 模型取决于配置的 OpenAI 兼容端点。
- 应用面向可信本机；生成代码会在本机受限进程中执行。

## 未来方向

规模化重建数据集、基准/评测流水线和 CAD 专用后训练属于项目方向。轨迹示例不代表已经
发布的数据集或训练完成的模型。

## 明确的契约限制

- Part 结果必须是一个有效且体积为正的 solid。
- Assembly 必须保留语义化 Part 边界，不能把独立零件熔成 Shape 来替代 Assembly。
- 一个 Project 不能并发运行两个 Agent turn。
- Prompt 上限为 32,000 个字符。
- 每个 Project 默认保留十个 Accepted 产物版本。
- Shape/Assembly 有效不代表强度、公差、可制造性、热性能或其他未实现的分析通过。
