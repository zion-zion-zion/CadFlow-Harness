# 零件与柔性几何

## 会话式零件

以下示例与返回一个 `cad.Shape` 的 Project 最接近：

```bash
uv run --locked --python 3.12 python examples/cadflow_complex_mounting_bracket.py
```

在受支持的 macOS 平台使用 `--python 3.13`。支架和 Sketch 支架示例会写出 STEP/STL，并打印测量值。要把它们用于 Project，请参考构造过程，改写到 `build_model(model)` 入口，不要复制示例的输出路径。

其他刚性示例包括 `cadflow_ceramic_cup.py`、`cadflow_apartment_floor_plan.py` 和 `cadflow_sun_wukong_portrait.py`。其中一些会生成多个 Shape 或渲染场景，因此只能作为 SDK 示例。

## 柔性几何

`cadflow_static_flexible_garment.py` 使用柔性 SDK 创建静态网格。它说明 CadFlow 也能处理刚性 BREP 之外的几何，但 Agent 执行器目前只接受 `cad.Shape` 或 `cad.Assembly`。柔性网格示例不能原样提交为 `code/model.py`。

`cadflow_flexible_jumpsuit.py` 使用标准 Shape 工作流构建刚性 BREP 近似结果，可作为单零件源码参考。
