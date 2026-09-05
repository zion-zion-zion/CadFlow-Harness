---
name: cadflow-model-part
description: Build or incrementally repair one rigid mechanical CAD part in CadFlow Harness. Use for dimensioned solids, sketches, profiles, holes, pockets, bosses, lofts, sweeps, blends, chamfers, and transforms. Use cadflow-model-assembly for separately manufactured bodies and cadflow-flexible-model for flexible shells.
---

# CadFlow Part Modeling

Build one reproducible positive-volume solid at a time. Use
`import cadflow as cad` and the public Python frontend. Read
`references/public-api.md` when a signature or operation boundary matters.

## Select the modeling boundary

- Implement the Harness entry point with the supplied `cad.Model` and return a
  `cad.Shape` containing exactly one connected solid.
- Use the session-oriented `cad.Model` operations for ordinary part features.
- Use `GraphSession`, `@cad.model`, `cad.capture_result()`, and the
  `make_*_r*` APIs when the result must be replayed through model JSON.
- Do not mix shapes created by different sessions.
- Do not import `cadflow._engine`, OCP/CadQuery classes, native handles, or
  private shared-library symbols in a part script.

## Required workflow

1. Inspect the current Project source and retain its coordinate frame,
   parameters, and working features unless the latest request changes them.
2. Extract units, dimensions, tolerances, datums, and feature order. State
   assumptions when the specification is incomplete.
3. Choose and document the coordinate frame. Keep all dimensions in one unit;
   convert at the input boundary rather than mixing units in operations.
4. Build named intermediate values in feature order: base, additive features,
   subtractive tools, transforms, then finishing operations.
5. Use sketches for dimensioned planar profiles. Promote a solved sketch to a
   wire/face before extrusion, revolution, or a profile cut.
6. Use one complete candidate for simple parts. For complex parts, stage
   materially distinct feature groups so every checkpoint remains a runnable,
   valid one-solid precursor.
7. Call `validate_model` after a planned checkpoint. Repair a failed checkpoint
   before adding later features. When every requested feature is present and
   validation passes, call `cad_review`.

The Harness runtime owns STEP, Scene, validation, and other product artifacts.
Model source returns geometry and does not export those artifacts itself.

## Modeling rules

- Treat operations as functional: retain useful source values instead of
  overwriting every feature.
- Keep a requested single part as one `Solid`; never hide a failed boolean by
  returning an arbitrary component of a compound.
- Query faces/edges before indexed fillet, chamfer, or shell selections.
  Topology indices can change after any topology-changing operation.
- Apply repeated holes or pockets sequentially unless fused tools are known to
  overlap and the fused tool has been validated.
- Use positive, finite dimensions and reject impossible radii before invoking
  a kernel operation.
- Keep graph recording and export parameters faithful. Do not claim replay
  equivalence when only a preview was checked.

## Minimal Harness pattern

```python
import cadflow as cad

def build_model(model: cad.Model) -> cad.Shape:
    base = model.box(width=20.0, depth=30.0, height=10.0)
    tool = model.cylinder(radius=3.0, height=14.0)
    tool = model.translate(tool, x=10.0, y=15.0, z=-2.0)
    final_part = model.cut(base, tool)
    return final_part
```

For replayable workflows, see `references/public-api.md` for the
`GraphSession`/`@cad.model` pattern.

## Failure handling

- Read the structured `validate_model` result before editing. Change the source
  materially before retrying; never repeat an unchanged candidate.
- For a failed boolean, inspect body/tool bounds, overlap, orientation, and
  validity before changing dimensions.
- For a failed feature, reduce the selection and radius only as a documented
  diagnostic experiment; do not silently weaken the requested geometry.
- If the specification is underdetermined, state the chosen assumption and
  expose it as a parameter in the script.
- For a timeout, simplify work in the reported execution phase while preserving
  requested geometry. Avoid unrelated detail or broad rewrites.
- When `cad_review` reports a substantive geometry or requirement finding,
  repair the source, validate the new revision, then review it again. A pure
  review-infrastructure failure does not justify changing valid geometry.
