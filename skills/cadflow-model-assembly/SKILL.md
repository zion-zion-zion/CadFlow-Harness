---
name: cadflow-model-assembly
description: Build, constrain, inspect, validate, and export complex multi-part mechanical products and nested subassemblies with CadFlow's public Python API. Use for assemblies with separately manufactured parts, repeated component instances, bearings, shafts, gears, motors, housings, connectors, kinematic joints, bills of materials, packaging, or collision/clearance checks. Use cadflow-model-part instead when the requested deliverable is one rigid solid without assembly semantics.
---

# CadFlow Assembly Modeling

Model a product structure, not a fused visual proxy. Preserve separately
manufactured parts, reusable component definitions, interfaces, and intended
degrees of freedom. Use `import cadflow as cad` and only the public frontend.

Read `references/project-structure.md` before writing a nested assembly or a
project with several component families. Read `references/assembly-api.md`
when choosing connector types, constraint functions, replay/export behavior,
or collision checks.

## Respect the delivery contract

Confirm the runtime's accepted return type before implementation. A full
assembly workflow may return or capture an `Assembly` plus a flattened preview
`Compound`. If the active executor requires exactly one positive-volume
`cad.Shape`, that run cannot deliver a true multi-part assembly. Preserve the
executor contract: report the incompatibility unless the user explicitly asks
for a single-solid proxy. Never fuse parts and call the result an assembly.

## Required workflow

1. Extract the product envelope, units, coordinate frame, power/load path,
   moving and fixed groups, standard parts, manufacturing boundaries,
   interfaces, clearances, expected motion, and requested outputs. Record
   uncertain values as named assumptions.
2. Build a design contract before geometry: a component inventory, interface
   table, assembly order, and quantitative invariants. Include gear ratios,
   shaft/bearing fits, fastener patterns, wall/ligament minima, and service
   access when relevant.
3. Centralize shared dimensions and validate their equations before invoking
   the kernel. A mating dimension has one source of truth consumed by both
   parts; do not repeat magic numbers across component modules.
4. Build and validate each unique `cad.Part` or reusable subassembly in its
   local coordinate frame. Give every Part the connectors its parent needs.
   Instantiate repeated parts from one definition rather than rebuilding
   identical geometry in a loop.
5. Create the assembly, add components with unique semantic IDs and authored
   placements, then forward only stable external connectors. Parent assemblies
   depend on public connectors, not on a child's private component paths.
6. Add the smallest independent constraint set that represents the physical
   joints. Ground the fixed load-path root, solve with `strict=True`, and
   inspect every unsolved component and residual. Initial placement is a seed
   and packaging statement; it is not proof of a solved relationship.
7. Validate system behavior at three levels: every leaf Part remains valid;
   the constraint report is solved within explicit tolerances; and the product
   meets envelope, clearance, motion, and interface invariants. Check multiple
   representative poses for moving products.
8. Project the solved assembly to preview geometry only after the semantic
   assembly passes. Run scoped static collision checks, classify every reported
   pair, and keep known limitations visible. Then export the requested STEP,
   model JSON, session JSON, metadata/BOM, and views, verifying non-zero files.
9. Report component and constraint counts, grounded roots, worst residuals,
   envelope, tested poses, collision scope/results, exported paths, and all
   unresolved engineering assumptions.

## Assembly rules

- A `Part` wraps exactly one solid. Use an `Assembly` for a bearing, motor,
  controller, or other reusable item with multiple separately moving bodies.
- Name connectors by mechanical role (`shaft_axis`, `bearing_outer_axis`,
  `case_mount_axis`), not topology index or incidental face order.
- Prefer face connectors for datums that must follow manufactured geometry.
  Use placement connectors for intentional abstract axes, pitch centers,
  service datums, and repeated patterns.
- Treat fixed, revolute, and prismatic constraints as joint semantics. Use gear,
  belt, and rack-pinion constraints only to couple already meaningful axes.
- External gear meshes rotate oppositely. An internal ring/planet mesh can use
  a same-direction belt relation with pitch radii when that is the intended
  kinematic abstraction; document this choice.
- Model bearing outer-ring and inner-ring attachment explicitly when their
  bodies participate in the solver. Decorative rolling elements do not need
  contact kinematics.
- Keep fastener holes and locating features geometrically compatible even when
  individual fastener solids are intentionally omitted.
- Constraint solving proves datum consistency, not strength, contact, torque,
  bearing life, backlash, thermal behavior, or manufacturability. State which
  claims still need engineering calculation or physical test.

## Failure handling

- For an unsolved assembly, inspect missing/duplicate connector references,
  contradictory grounds, cycles, authored placements, and the first non-zero
  residual before changing geometry.
- For overconstraint, remove redundant joint equations or choose a single
  kinematic abstraction; do not weaken strict solving to hide the conflict.
- For collision failures, distinguish unintended penetration from intentional
  fits, gear engagement, or decorative overlap. Scope exclusions narrowly and
  record the physical reason for each excluded pair.
- A passing current-pose collision report is not swept-volume clearance. Sample
  motion poses; note that the current verifier can miss complete containment.
- If packaging fails, return to the dimension and interface contract. Do not
  shrink unrelated parts or silently violate the requested envelope.
