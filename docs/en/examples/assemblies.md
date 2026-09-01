# Assemblies and reconstruction

## Product assemblies

The two modular product examples use replayable CadFlow constructors and a
semantic Assembly boundary:

```bash
uv run --locked --python 3.12 python examples/16_compact_two_stage_planetary_reducer/main.py
uv run --locked --python 3.12 python examples/20_integrated_bldc_joint_actuator/main.py
```

Use Python 3.13 on the supported macOS platform. These examples show how to
share Part definitions, place repeated occurrences, add connectors and
constraints, and export an assembly. They are broader SDK references than the
default shape of a simple Project request.

## Text2CAD-derived workpieces

`text2cad_workpiece.py`, `text2cad_complex_workpiece.py`,
`text2cad_connector_workpiece.py`, and `text2cad_boiler.py` rebuild progressively
more complex feature sequences and export STEP/STL/preview files. They are
useful for studying explicit feature order and diagnostics; their output paths
are example-specific.

## Reconstruction data seed

`examples/agentic_reconstruction_dataset/` contains adapters and
`generate_example.py` for one tool-rich reconstruction trajectory. It shows how
observable tool calls and geometric outcomes can be packaged without hidden
chain-of-thought. It is a seed example, not a production-scale dataset or
training pipeline.
