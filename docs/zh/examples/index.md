# 示例目录

示例是可运行的 CadFlow 程序和 SDK 参考，不是可以直接复制到 Agent Project 的源码。按
[快速开始](../quickstart.md)选择平台 Python 后，从仓库根目录运行。除非示例另有说明，
生成文件会写入被 Git 忽略的 `examples/out/`。

| 类别 | 入口 | 展示内容 |
| --- | --- | --- |
| 单个刚性零件 | [`cadflow_complex_mounting_bracket.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_complex_mounting_bracket.py) | 使用面向会话的 `cad.Model` API 构建加强支架。 |
| 有尺寸的 Sketch 重建 | [`cadflow_sketch_support_bracket.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_sketch_support_bracket.py) | Sketch 特征、孔、诊断以及 STEP/STL 导出。 |
| 柔性外观几何 | [`cadflow_flexible_jumpsuit.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_flexible_jumpsuit.py) | 用刚性 BREP 表示服装风格结果。 |
| 静态柔性网格 | [`cadflow_static_flexible_garment.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_static_flexible_garment.py) | `cadflow.flexible` SDK；不满足应用的 Shape/Assembly 契约。 |
| 模块化装配 | [`16_compact_two_stage_planetary_reducer`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/16_compact_two_stage_planetary_reducer) | 可重放的齿轮、轴、行星架、壳体和约束。 |
| 产品装配 | [`20_integrated_bldc_joint_actuator`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/20_integrated_bldc_joint_actuator) | 模块化电机、电子件、减速器和壳体产品。 |
| 重建轨迹 | [`agentic_reconstruction_dataset`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/agentic_reconstruction_dataset) | 未来数据工作的工具记录与适配器种子示例。 |

[仓库 examples README](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/README.md)包含完整清单，
包括 Text2CAD 派生工件和渲染示例。
