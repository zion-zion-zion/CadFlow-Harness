# Parts and flexible geometry

## Session-oriented parts

These examples are closest to a Project that returns one `cad.Shape`:

```bash
uv run --locked --python 3.12 python examples/cadflow_complex_mounting_bracket.py
```

Use `--python 3.13` on the supported macOS platform. The bracket and sketch
support examples write STEP/STL files and print measurements. To use their ideas
in a Project, adapt the construction to `build_model(model)` instead of copying
the example output paths.

Other rigid examples include `cadflow_ceramic_cup.py`,
`cadflow_apartment_floor_plan.py`, and `cadflow_sun_wukong_portrait.py`. Some
produce several shapes or a rendering scene, so they are SDK examples rather
than direct Project contracts.

## Flexible geometry

`cadflow_static_flexible_garment.py` uses the flexible SDK to create a static
mesh. It shows the SDK beyond rigid BREP, but the Agent executor currently
accepts only a `cad.Shape` or `cad.Assembly`. Do not submit the mesh example
unchanged as `code/model.py`.

`cadflow_flexible_jumpsuit.py` builds a rigid BREP approximation with the
standard Shape workflow and is a useful single-part source example.
