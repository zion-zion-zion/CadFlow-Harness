# 装配与重建

## 产品装配

两个模块化产品示例使用可重放的 CadFlow 构造器和语义化 Assembly 边界：

```bash
uv run --locked --python 3.12 python examples/16_compact_two_stage_planetary_reducer/main.py
uv run --locked --python 3.12 python examples/20_integrated_bldc_joint_actuator/main.py
```

在受支持的 macOS 平台使用 Python 3.13。这些示例展示共享 Part 定义、放置重复实例、添加
连接器和约束以及装配导出；它们比普通 Project 需求需要更完整的 SDK 能力。

## Text2CAD 派生工件

`text2cad_workpiece.py`、`text2cad_complex_workpiece.py`、`text2cad_connector_workpiece.py`
和 `text2cad_boiler.py` 重建逐步复杂的特征序列，并导出 STEP/STL/预览文件。它们适合
研究显式特征顺序和诊断，输出路径由各示例自行定义。

## 重建数据种子

`examples/agentic_reconstruction_dataset/` 包含适配器以及 `generate_example.py`，用于生成
一次工具丰富的重建轨迹。它展示如何在不保存隐藏思维链的情况下组织工具调用和几何结果。
这是种子示例，不是生产级数据集或训练流水线。
