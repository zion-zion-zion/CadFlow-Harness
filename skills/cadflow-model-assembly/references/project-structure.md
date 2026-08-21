# Complex Assembly Project Structure

Use this layout when the workspace permits multiple modules. Keep dependencies
flowing upward so component modules never import the top-level assembly.

```text
product_name/
|-- DESIGN.md          Product intent, BOM, interfaces, assembly order, limits
|-- dimensions.py      Units, dataclasses, shared dimensions, invariant checks
|-- materials.py       Reusable material definitions
|-- common.py          Connector, placement, grounding, and query helpers
|-- housing.py         One component family per focused module
|-- shafts.py
|-- bearings.py
|-- gears.py
|-- assembly.py        Inventory, instances, public connectors, constraints
|-- main.py            Replayable build, validation summary, and export
`-- collision_probe.py Optional focused verifier for expensive collision runs
```

Under the CadFlowAgent single-file entry contract, keep `model.py` as the
required entry point and place the same responsibilities in local helper
modules. The active executor contract still decides which final value can be
returned; module layout does not broaden it.

## Design contract

Before detailed geometry, record enough information to detect a plausible but
wrong assembly:

- Product classification, coordinate frame, fixed root, moving groups, power
  or load path, and external interfaces.
- BOM rows with semantic ID, quantity, Part/subassembly type, material, and
  manufacturing intent.
- Interface rows with the two participants, joint type, real connection,
  connector IDs, fits/clearances, and required geometry.
- Assembly order, including insertion paths, removable caps, trapped parts,
  tool access, and service access.
- Quantitative invariants such as envelope, coaxial offsets, minimum walls,
  pitch-center equations, gear tooth relationships, bearing stack lengths,
  bolt-circle ligaments, and connector aperture clearances.

Put invariant checks in a function such as `validate_design_dimensions()` and
call it before constructing any solids. Check positivity and finiteness as well
as product-specific equations. Error messages should name the failed physical
relationship and measured values.

## Module contracts

`dimensions.py` is the source of truth for mating geometry. Small immutable
dataclasses work well for standards and repeated families, such as bearing or
gear-stage specifications. Derive pitch radii, center distances, and ratios
from those specifications instead of storing competing values.

Component modules return `cad.Part` or reusable `cad.Assembly` values in local
coordinates. They expose connector IDs through their public return value and
do not know parent component IDs. Use names such as `make_output_carrier_rpart`
and `make_motor_rassembly` when the repository follows CadFlow result-suffix
naming.

`assembly.py` owns:

- the component inventory and unique instance IDs;
- repeated-instance placements;
- assembly-level public connector forwarding;
- grounding and joint constraints; and
- strict solving plus a compact constraint report.

`main.py` owns the replayable `@cad.model` entry, dimension validation,
assembly preview projection, captured results, JSON/STEP export, and the final
quantitative summary. Keep an expensive collision probe separate when it is
useful to iterate on geometry without rerunning every pair check.

## Instance and connector design

Build one definition for each repeated standard part, then add it under unique
component IDs and placements. The identity of an instance belongs to the
parent assembly; the identity of its connector belongs to the reusable item.

Use connectors as a public mechanical API:

- A leaf Part exposes only datums meaningful to a parent.
- A subassembly forwards stable case, input, output, mounting, or service
  datums from internal components.
- A parent constrains against forwarded connector IDs and remains independent
  of the child's private component tree.

This boundary makes a bearing, reducer, motor, or controller replaceable
without rewriting every parent constraint.

## Constraint graph

Sketch the constraint graph before encoding it. Mark the grounded root, rigid
groups, revolute/prismatic degrees of freedom, and motion-coupling edges.
Choose one physical interpretation per relationship. For example, a bearing
interface should not also receive a conflicting fixed constraint merely because
the initial placements coincide.

Seed each component near its intended pose. Add structural constraints first,
primary joints second, repeated joint families third, and motion couplings
last. Solve and inspect after each meaningful group when diagnosing a large
graph. A component count plus constraint count is not sufficient; the final
report must have no unexplained unsolved components and residuals within the
chosen tolerance.

## Verification matrix

Use a compact matrix so every important claim has evidence:

| Claim | Evidence |
|---|---|
| Leaf geometry is usable | validity, positive volume, topology, bounds |
| Mating interfaces agree | shared dimensions plus connector-frame checks |
| Product structure is complete | expected component IDs and quantities |
| Kinematics are coherent | strict solve, residuals, driven pose checks |
| Package fits | analytical envelope plus preview bounds |
| Static pose is clear | scoped collision report with classified exclusions |
| Motion remains clear | collision checks at representative/limit poses |
| Result is reproducible | model/session JSON replay and checked exports |

Collision and kinematic checks do not replace tooth stress, bearing life,
fastener preload, tolerance-stack, thermal, electromagnetic, fatigue, or
prototype validation. Report those as unresolved engineering analyses when
they matter to the product.
