---
name: cadflow-model-assembly
description: Build and validate semantic multi-part mechanical products with CadFlow. Use for separately manufactured parts, repeated instances, nested subassemblies, connectors, constraints, BOM structure, or envelopes. Use cadflow-model-part when the deliverable is one rigid manufactured solid.
---

# CadFlow Assembly Modeling

Model the product structure rather than a fused visual proxy. Preserve unique
Part definitions, repeated occurrences, stable interfaces, constraints, and
intended degrees of freedom. Use only the public `cadflow` frontend.

Read `references/project-structure.md` when the product has several component
families, repeated instances, or nested subassemblies. Read
`references/assembly-api.md` for exact Part, connector, constraint, and runtime
contract syntax.

## Runtime contract

Keep `/code/model.py` as the stable entry point:

```python
def build_model(model: cad.Model) -> cad.Assembly:
    return make_product_rassembly()
```

Use focused helper modules under `/code/` for complex products. After the early
Assembly gates pass, the executor loads the complete Python source tree and
generates the semantic model, Scene, product STEP, one STEP per unique Part,
BOM, assumptions, validation report, and deterministic source snapshot. Source
code does not write those outputs.

Define JSON-compatible `PRODUCT_SPEC` in `model.py`. Every Assembly declares
`envelope.max_size_mm`; record inferred values in `assumptions`.

## Workflow

1. Classify the product into separately manufactured Parts and nested
   subassemblies. Record the coordinate frame, fixed root, load or power path,
   moving groups, external interfaces, envelope, and unresolved assumptions.
2. Establish one shared dimension source. Encode product equations and
   positivity, fit, wall, spacing, and packaging invariants before geometry.
3. Build each unique Part in local coordinates with the replayable `cad.Solid`
   API described in the Assembly reference. Keep every union connected. Add
   only the stable mechanical connectors its parent needs, and reuse the same
   Part object for repeated occurrences.
4. Add components with unique semantic IDs and explicit seed placements.
   Forward stable connectors from nested subassemblies instead of exposing
   their private paths.
5. Encode the smallest independent constraint set that represents the physical
   joints. Ground the fixed load-path root and preserve intended revolute or
   prismatic freedom.
6. Return the semantic Assembly and run `validate_model`. Treat strict solve,
   every residual's SDK tolerance, STEP replay, Scene parse, envelope, and
   product structure as blocking gates.
7. Repair one diagnosed failure at a time. A successful partial Assembly is a
   checkpoint; continue until the complete requested product is represented.
   Run `cad_review` only after deterministic validation reports `Passed`.

## Assembly rules

- A `cad.Part` wraps exactly one solid. A bearing, motor, or other item with
  separately moving bodies is a nested `cad.Assembly`.
- A component occurrence has a unique path; a repeated manufactured item keeps
  one Part definition and appears at several paths. Do not rebuild identical
  geometry in an instance loop.
- Name connectors by mechanical role (`shaft_axis`, `bearing_outer_axis`,
  `case_mount`) rather than topology index.
- Prefer face connectors for manufactured datums and placement connectors for
  abstract axes, pitch centers, service datums, and patterns.
- Treat fixed, revolute, and prismatic constraints as joint semantics. Motion
  coupling constraints relate already meaningful joint axes; they do not
  construct teeth, contact, backlash, or load capacity.
- Keep fastener holes and locating features compatible even when fastener
  solids are intentionally omitted.
- Do not claim motion clearance, strength, bearing life, tolerance stack,
  thermal performance, or manufacturability without corresponding analysis.

## Failure handling

- Read the failed entries in `product_validation_checks` before editing. Use
  their solve message, residual IDs, or envelope measurements as the repair
  scope. A short-circuited diagnostic Draft omits
  downstream artifacts; the complete report enters the product bundle after
  the early gates pass.
- A failed strict solve may include non-strict diagnostic residuals. Use that
  evidence for repair, then return the semantic Assembly normally; remove
  temporary solve calls and debug prints before final validation.
- For a strict-solve failure, inspect the first residual, missing connector,
  contradictory ground, duplicate reference, and constraint cycle before
  changing geometry.
- For envelope failure, repair shared dimensions or packaging while preserving
  the requested interfaces and function.
- For STEP or Scene failure, simplify or repair the failing Part topology while
  retaining semantic Assembly boundaries.
- For timeout, use `execution_phase` and the phase named in `error`. Repair the
  active phase instead of changing unrelated modules. Simplify nonfunctional
  detail before retrying a slow build, STEP, Scene, or review phase.
