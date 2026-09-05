---
name: cadflow-scene-presentation
description: Add requested colors, paint, metalness, roughness, edge styling, or cameras to a CadFlow Harness model through the Scene Presentation contract. Use for explicit visual-finish requests; use geometry Skills for shape changes.
---

# CadFlow Scene Presentation

Represent requested visual finish as presentation metadata, separate from
manufactured geometry. Do not add decorative solids to imitate paint or
material appearance.

Define a JSON-compatible `PRESENTATION` mapping in `/code/model.py`. The Harness
runtime applies it to the compiled geometry and includes it in the final Scene.
Read `references/presentation-api.md` for the exact schema, occurrence node IDs,
and a minimal example.

## Workflow

1. Preserve the existing geometry and choose appearances from explicit user
   requests. Do not infer engineering material properties from color alone.
2. Map each appearance to stable Shape or Part occurrence nodes. Target leaf
   occurrences, not Assembly grouping nodes.
3. Add cameras only when the request needs a defined view. Keep camera data
   independent of modeling coordinates and constraints.
4. Run `validate_model` so the runtime can verify node references and Scene
   output. Run `cad_review` after the complete product passes validation.

When incrementally editing an existing presentation, retain unaffected
appearances, node assignments, and cameras. Change only the requested visual
properties.
