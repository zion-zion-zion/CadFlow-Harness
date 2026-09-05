# CadFlow Scene Presentation 1.0

`PRESENTATION` is an optional JSON-compatible mapping in `/code/model.py`.
Use `source_scene_id="model"` and a stable `presentation_id`.

## Node IDs

- A single returned Shape uses `instance/main`.
- Assembly Part occurrences use `instance/main/<component_id>/...`.
- Apply appearance overrides to Shape or Part occurrence nodes, not Assembly
  grouping nodes.

## Appearance fields

Each appearance provides:

- `name`: unique appearance name;
- `base_color`: RGBA values in the inclusive range 0 to 1;
- `metallic` and `roughness`: values in the inclusive range 0 to 1;
- `alpha_mode`: use `"opaque"` for ordinary solid products;
- `double_sided`: normally `False` for closed solids; and
- `edge_color`: RGBA edge-line color.

The top-level mapping contains `schema_version="1.0"`, `presentation_id`,
`source_scene_id`, `appearances`, `node_overrides`, and `cameras`.

## Minimal example

Replace the node ID and values for the actual product:

```python
PRESENTATION = {
    "schema_version": "1.0",
    "presentation_id": "requested-finish",
    "source_scene_id": "model",
    "appearances": [{
        "name": "painted-metal",
        "base_color": [0.05, 0.20, 0.80, 1.0],
        "metallic": 0.7,
        "roughness": 0.25,
        "alpha_mode": "opaque",
        "double_sided": False,
        "edge_color": [0.03, 0.03, 0.05, 1.0],
    }],
    "node_overrides": [{
        "node_id": "instance/main",
        "appearance_name": "painted-metal",
    }],
    "cameras": [],
}
```

The runtime rejects malformed schemas or node references through normal model
validation. Keep this mapping declarative; source code does not write or patch
Scene artifacts directly.
