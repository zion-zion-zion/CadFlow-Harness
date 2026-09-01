# 装配与重建

## 产品装配

两个模块化产品示例使用可重放的 CadFlow 构造器，并返回语义化 Assembly：

```bash
uv run --locked --python 3.12 python examples/16_compact_two_stage_planetary_reducer/main.py
uv run --locked --python 3.12 python examples/20_integrated_bldc_joint_actuator/main.py
```

在受支持的 macOS 平台使用 Python 3.13。示例会复用 Part 定义，放置重复实例，添加连接器和约束，然后导出装配结果。它们需要的 SDK 接口比简单 Project 更多。

## Text2CAD 派生工件

`text2cad_workpiece.py`、`text2cad_complex_workpiece.py`、`text2cad_connector_workpiece.py` 和 `text2cad_boiler.py` 按特征顺序重建模型，并导出 STEP、STL 和预览文件。输出路径由各示例自行定义。

## 重建数据示例

`examples/agentic_reconstruction_dataset/` 包含适配器和 `generate_example.py`，用于生成一条带工具调用的重建轨迹。它是数据格式示例，不是生产级数据集或训练流水线。
