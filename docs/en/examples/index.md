# Example catalog

Examples are runnable CadFlow programs and SDK references, not drop-in Agent
Project source. Run them from the repository root with the platform Python
selected by [Quick start](../quickstart.md). Generated files go under ignored
`examples/out/` unless an example documents another output path.

| Family | Start here | What it demonstrates |
| --- | --- | --- |
| Single rigid part | [`cadflow_complex_mounting_bracket.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_complex_mounting_bracket.py) | A reinforced bracket built with the session-oriented `cad.Model` API. |
| Dimensioned sketch reconstruction | [`cadflow_sketch_support_bracket.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_sketch_support_bracket.py) | Sketch-driven features, holes, diagnostics, and STEP/STL export. |
| Flexible-looking geometry | [`cadflow_flexible_jumpsuit.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_flexible_jumpsuit.py) | A garment-like result represented as a rigid BREP. |
| Static flexible mesh | [`cadflow_static_flexible_garment.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_static_flexible_garment.py) | The `cadflow.flexible` SDK; it does not satisfy the app's Shape/Assembly contract. |
| Modular assembly | [`16_compact_two_stage_planetary_reducer`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/16_compact_two_stage_planetary_reducer) | Replayable gears, shafts, carriers, housing, and constraints. |
| Product assembly | [`20_integrated_bldc_joint_actuator`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/20_integrated_bldc_joint_actuator) | A modular motor, electronics, reducer, and housing product. |
| Reconstruction trajectory | [`agentic_reconstruction_dataset`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/agentic_reconstruction_dataset) | A small tool-rich record and adapter example for future data work. |

The [repository examples README](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/README.md)
has the complete list, including Text2CAD-derived workpieces and rendering
examples.
