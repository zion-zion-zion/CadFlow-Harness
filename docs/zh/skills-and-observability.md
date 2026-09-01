# Skills 与可观察性

## CAD Skills

Skills 是按任务加载的 Markdown 参考，以只读方式挂载到 Agent Run。公开目录如下：

| Skill | 用途 |
| --- | --- |
| [`cadflow-model-part`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-part) | 有尺寸的刚性零件、Sketch、特征、布尔、圆角和单 Part 交付。 |
| [`cadflow-flexible-model`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-flexible-model) | 静态布料、皮革、薄膜、服装和其他柔性几何。 |
| [`cadflow-step-brep`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-step-brep) | STEP/BREP 检查、重建、截面分析和基于证据的比较。 |
| [`cadflow-model-assembly`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-assembly) | 多部件产品、放置、连接器、约束和验收。 |
| [`cadflow-rotary-transmission`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-rotary-transmission) | 齿轮、轴、轴承、壳体和旋转机构。 |

Skills 提供实现指导，但不会扩大执行器契约。Agent 仍然必须返回当前运行时能够接受的有效
`Shape` 或语义化 `Assembly`。

## 可观察记录

每个 turn 可以产生对话 JSONL、进度事件、源码版本、执行诊断、几何测量、token 数量和产品
产物。提供商凭证与大载荷会被限制或脱敏。API 提供实时 SSE 事件、Trace 路由和脱敏 NDJSON
下载。

这些记录让失败和修复可以测量，同时不保存隐藏思维链。它们未来可以支持评测或数据整理，
但当前仓库只有一个小型重建数据示例，尚未提供生产级数据流程。
