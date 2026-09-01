# Example catalog

These examples are runnable CadFlow programs and SDK references. They are not
drop-in Agent Project source. Run them from the repository root with the platform
Python selected by [Quick start](../quickstart.md). Generated files go under
ignored `examples/out/` unless an example says otherwise.

| Family | Start here | What it shows |
| --- | --- | --- |
| Single rigid part | [`cadflow_complex_mounting_bracket.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_complex_mounting_bracket.py) | A reinforced bracket built with the session-oriented `cad.Model` API. |
| Dimensioned sketch reconstruction | [`cadflow_sketch_support_bracket.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_sketch_support_bracket.py) | Sketch features, holes, diagnostics, and STEP/STL export. |
| Flexible-looking geometry | [`cadflow_flexible_jumpsuit.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_flexible_jumpsuit.py) | A garment-like result represented as a rigid BREP. |
| Static flexible mesh | [`cadflow_static_flexible_garment.py`](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/cadflow_static_flexible_garment.py) | The `cadflow.flexible` SDK. Its result does not satisfy the app's Shape/Assembly contract. |
| Modular assembly | [`16_compact_two_stage_planetary_reducer`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/16_compact_two_stage_planetary_reducer) | A planetary reducer with gears, shafts, carrier, housing, and constraints. |
| Product assembly | [`20_integrated_bldc_joint_actuator`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/20_integrated_bldc_joint_actuator) | A motor, electronics, reducer, and housing assembled as one product. |
| Reconstruction trajectory | [`agentic_reconstruction_dataset`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/examples/agentic_reconstruction_dataset) | One tool-rich record and adapter for reconstruction data. |

The [examples README](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/examples/README.md)
has the full list, including Text2CAD-derived workpieces and rendering examples.
