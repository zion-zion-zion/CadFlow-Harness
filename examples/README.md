# CadFlow Examples

Run examples from the repository root with Python 3.12. Generated files are
written under `examples/out/` and ignored by Git.

## Python-first Model/Shape examples

These are the closest references for the Agent application's current
single-rigid-part `model.py` contract:

- `cadflow_complex_mounting_bracket.py`
- `cadflow_sketch_support_bracket.py`
- `cadflow_apartment_floor_plan.py`
- `cadflow_flexible_jumpsuit.py` (a flexible-looking garment represented as a
  rigid BREP)
- `cadflow_sun_wukong_portrait.py`

```bash
uv run python examples/cadflow_complex_mounting_bracket.py
```

Adapt the construction techniques rather than copying an example entry point:
the application runner still requires `build_model(model) -> cad.Shape` and one
solid.

## Static flexible mesh

`cadflow_static_flexible_garment.py` uses `cadflow.flexible` to build and export
a static mesh. It demonstrates the flexible SDK but does not satisfy the
application's current `cad.Shape` return contract.

```bash
uv run python examples/cadflow_static_flexible_garment.py
```

## Replayable and compatibility APIs

- `cadflow_ceramic_cup.py` uses replayable solid constructors.
- `cadflow_weeping_willow.py` combines replayable constructors with specialized
  rendering helpers.
- `16_compact_two_stage_planetary_reducer/` is a modular replayable assembly.
- `20_integrated_bldc_joint_actuator/` is a modular motor and reducer assembly.

```bash
uv run python examples/16_compact_two_stage_planetary_reducer/main.py
uv run python examples/20_integrated_bldc_joint_actuator/main.py
```

These examples cover CadFlow SDK capabilities beyond the Agent application's
single-part Model/Shape boundary.

## Text2CAD workpieces

The synchronized Text2CAD examples build and export progressively more complex
mechanical workpieces:

- `text2cad_workpiece.py`
- `text2cad_complex_workpiece.py`
- `text2cad_connector_workpiece.py`
- `text2cad_boiler.py`

Their default output paths point to CadFlow's repository-level `artifacts/`
layout. Override those paths before running them from another checkout when
the generated artifacts should stay inside this repository.
