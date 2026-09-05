# Skills 与运行记录

## CAD Skills

Skills 是按任务加载的 Markdown 参考文件，Run 中以只读方式挂载。当前公开目录如下：

| Skill | 用途 |
| --- | --- |
| [`cadflow-model-part`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-part) | 有尺寸的刚性零件、Sketch、特征、布尔、圆角和单 Part 交付。 |
| [`cadflow-flexible-model`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-flexible-model) | 静态布料、皮革、薄膜、服装和其他柔性几何。 |
| [`cadflow-step-brep`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-step-brep) | STEP/BREP 检查、重建、截面分析和基于测量值的比较。 |
| [`cadflow-model-assembly`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-assembly) | 多部件产品、定位、连接器、约束和验收。 |
| [`cadflow-rotary-transmission`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-rotary-transmission) | 齿轮、轴、轴承、壳体和旋转机构。 |
| [`cadflow-scene-presentation`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-scene-presentation) | 用户指定的颜色、材质外观、边线样式和相机。 |

Skills 只提供实现参考，不会改变执行器契约。Agent 仍须返回当前运行时能接受的有效 `Shape` 或语义化 `Assembly`。

## 运行记录

每个 turn 可以写入对话 JSONL、进度事件、源码版本、执行诊断、几何测量、token 数量和产品产物。提供商凭证与大载荷会被限制或脱敏。API 提供实时 SSE、Trace 路由和脱敏 NDJSON 下载。

这些记录可用于定位失败和修复过程，不包含隐藏思维链。仓库目前只有一个小型重建数据示例，没有生产级的数据处理流程。
