# Parts and flexible geometry

## Session-oriented parts

These examples are closest to a Project that returns one `cad.Shape`:

```bash
uv run --locked --python 3.12 python examples/cadflow_complex_mounting_bracket.py
```

On macOS, use `--python 3.13`. The bracket and sketch-support examples write
STEP/STL files and print measured diagnostics. Adapt their construction ideas
to a Project's `build_model(model)` entry point; do not copy their output paths
into a Run.

Other useful rigid examples include `cadflow_ceramic_cup.py`,
`cadflow_apartment_floor_plan.py`, and `cadflow_sun_wukong_portrait.py`. Some
produce several shapes or rendering scenes and therefore are SDK demonstrations
rather than direct Project contracts.

## Flexible geometry

`cadflow_static_flexible_garment.py` uses the flexible SDK to create a static
mesh. It is valuable for understanding that CadFlow supports more than rigid
BREP workflows, but the current Agent executor still requires a `cad.Shape` or
`cad.Assembly` result. A flexible mesh example cannot be submitted unchanged
as `code/model.py`.

`cadflow_flexible_jumpsuit.py` is intentionally different: it builds a rigid
BREP approximation with the standard Shape workflow and can be studied as a
single-part source example.
