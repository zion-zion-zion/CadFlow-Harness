# 零件与柔性几何

## 会话式零件

以下示例最接近返回一个 `cad.Shape` 的 Project：

```bash
uv run --locked --python 3.12 python examples/cadflow_complex_mounting_bracket.py
```

在受支持的 macOS 平台使用 `--python 3.13`。支架和 Sketch 支架示例会写出 STEP/STL 并
打印诊断数据。应将其建模思路改写到 Project 的 `build_model(model)` 入口，不要把示例
输出路径复制到 Run 中。

其他刚性示例包括 `cadflow_ceramic_cup.py`、`cadflow_apartment_floor_plan.py` 和
`cadflow_sun_wukong_portrait.py`。其中一些会产生多个 Shape 或渲染场景，因此是 SDK 演示，
不是直接的 Project 契约。

## 柔性几何

`cadflow_static_flexible_garment.py` 使用柔性 SDK 创建静态网格，可以帮助理解 CadFlow
不仅支持刚性 BREP，但当前 Agent 执行器仍要求 `cad.Shape` 或 `cad.Assembly`。柔性网格
示例不能原样作为 `code/model.py` 提交。

`cadflow_flexible_jumpsuit.py` 则不同：它用标准 Shape 工作流构建刚性 BREP 近似结果，可
作为单零件源码示例研究。
