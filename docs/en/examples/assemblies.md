# Assemblies and reconstruction

## Product assemblies

These two modular product examples use replayable CadFlow constructors and
return a semantic Assembly:

```bash
uv run --locked --python 3.12 python examples/16_compact_two_stage_planetary_reducer/main.py
uv run --locked --python 3.12 python examples/20_integrated_bldc_joint_actuator/main.py
```

Use Python 3.13 on the supported macOS platform. The examples reuse Part
definitions, place repeated instances, add connectors and constraints, and
export the assembly. They use more SDK APIs than a simple Project.

## Text2CAD-derived workpieces

`text2cad_workpiece.py`, `text2cad_complex_workpiece.py`,
`text2cad_connector_workpiece.py`, and `text2cad_boiler.py` rebuild models in
feature order and export STEP, STL, and preview files. Each example chooses its
own output path.

## Reconstruction data example

`examples/agentic_reconstruction_dataset/` contains adapters and
`generate_example.py` for one reconstruction trajectory with tool calls. It is
an example of the data format, not a production dataset or training pipeline.
