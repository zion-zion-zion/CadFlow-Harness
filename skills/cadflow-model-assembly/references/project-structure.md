# Complex Assembly Workspace

Split source by stable responsibility when several component families,
repeated definitions, or nested subassemblies would make one file difficult to
repair. Small assemblies can remain in `model.py`; file count is not a quality
target.

```text
/code/
|-- model.py          Stable build_model entry and PRODUCT_SPEC
|-- dimensions.py     Dataclasses, shared dimensions, derived values, invariants
|-- common.py         Connector, placement, and repeated construction helpers
|-- housing.py        One cohesive manufactured Part family per module
|-- shafts.py
|-- gears.py
|-- bearings.py       Standard or custom reusable definitions
`-- assembly.py       Inventory, occurrences, public interfaces, constraints
```

Keep dependencies flowing upward:

```text
model -> assembly -> component families -> dimensions/common
```

Component modules never import the top-level assembly. Use ordinary sibling
imports because `/code/` is on the runtime import path. Remove helpers that are
obsolete after a repair so the source snapshot has one clear implementation.

## Design contract

Before detailed geometry, encode enough information to reject a plausible but
wrong product:

- product class, coordinate frame, fixed root, moving groups, and power/load
  path;
- unique Part inventory, quantities, material or manufacturing intent;
- each mating interface, joint type, connectors, fits, and required geometry;
- assembly and service access constraints when they affect geometry; and
- quantitative envelope, pitch-center, coaxial, wall, ligament, bearing-stack,
  bolt-pattern, and clearance invariants that apply.

Put dimensions shared by mating Parts in `dimensions.py`. Immutable dataclasses
are useful for repeated standards or transmission stages. Derive pitch radii,
center distances, ratios, and patterns instead of storing competing values.
Call a function such as `validate_design_dimensions()` before constructing any
solid. Its errors should name the failed physical relationship and values.

`PRODUCT_SPEC` is a runtime contract, not a substitute for design equations:

```python
PRODUCT_SPEC = {
    "assumptions": ["Torque and bearing-life calculations are out of scope."],
    "envelope": {"max_size_mm": [80.0, 80.0, 120.0]},
}
```

## Module contracts

Each component factory returns a `cad.Part` or reusable `cad.Assembly` in local
coordinates. It exposes parent-facing connector IDs but knows no parent
component IDs. `assembly.py` owns unique occurrence IDs, seed placements,
connector forwarding, grounding, and joint constraints. `model.py` imports
`PRODUCT_SPEC` or defines it directly and delegates `build_model` to the
assembly factory.

Build one definition per repeated manufactured item, then add it under unique
component IDs. The identity of an occurrence belongs to its parent; connector
identity belongs to the reusable definition. A parent constrains nested items
through forwarded case, input, output, mounting, or service connectors.

## Constraint graph

Sketch the grounded root, rigid groups, remaining degrees of freedom, and
motion-coupling edges before encoding constraints. Seed each component near its
intended pose. Add structural constraints first, primary joints second,
repeated joint families third, and motion couplings last. Choose one physical
interpretation per relationship; do not add a fixed constraint on top of a
revolute interface just because the seed placements coincide.

## Evidence matrix

| Claim | Automatic or source evidence |
|---|---|
| Leaf geometry is usable | validity, one positive solid per Part, volume |
| Product structure is complete | semantic tree, unique Part records, BOM |
| Mating interfaces agree | shared dimensions, connector frames, strict solve |
| Constraints are coherent | no unsolved components, every residual in tolerance |
| Package fits | replayed product STEP bounds against `PRODUCT_SPEC` |
| Result is reproducible | complete hash-bound Python source snapshot |

The executor generates and verifies all product artifacts. Keep any unperformed
stress, life, preload, tolerance, thermal, fatigue, or prototype analysis
explicit in assumptions rather than presenting it as validated.
