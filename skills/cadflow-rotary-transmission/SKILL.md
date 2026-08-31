---
name: cadflow-rotary-transmission
description: Design compact semantic rotary-transmission assemblies with CadFlow. Use for spur, helical, herringbone, bevel, planetary, compound-gear, belt, pulley, shaft, bearing, housing, gearbox, reducer, or speed-increaser requests. Use cadflow-model-assembly for non-transmission multi-part products.
---

# CadFlow Rotary Transmission

Build a mechanically legible transmission, not a collection of decorative
solids. Read `/skills/cadflow-model-assembly/SKILL.md` and its Assembly API
reference first. Read `references/design-rules.md` before choosing tooth counts
or placements. Read `references/component-api.md` when constructing gears or a
standard ball bearing.

## Workflow

1. Extract input/output axes, ratio and direction, envelope, mounting
   interfaces, fixed members, moving members, and any explicitly requested
   topology. When topology is open, choose the simplest arrangement that meets
   those constraints.
2. Select integer tooth counts and shared gear parameters. Derive pitch radii,
   center distances, stage ratios, planet locations, and overall ratio in one
   dimension module. Assert every governing equation before geometry.
3. Define the manufactured inventory: gears or pulleys, shafts, carriers,
   housing and covers, bushings or bearings, and interface flanges. Reuse one
   definition for repeated planets, bearings, and fasteners.
4. Build gears with `cad.std.gear` public constructors. Add real bores and
   shaft/housing seats where requested. Keep each Part one connected solid;
   use an Assembly whenever bodies are separately manufactured or move
   independently.
5. Place components from pitch geometry rather than visual trial. Give every
   rotating item an axis connector; give housing and carriers matching bearing,
   pin, input, output, and mounting connectors.
6. Ground the housing or fixed ring. Add physical fixed/revolute joints first,
   then gear, belt, or rack-pinion couplings between established axes. Encode
   external meshes with inverse-direction gear coupling and same-direction
   internal planetary meshes with the documented kinematic abstraction.
7. Validate in stages: one representative Part per family, one functional
   stage, then the complete product. Repair strict-solve and packaging failures
   before adding detail.
8. Verify the complete product passes strict constraint solving, every residual,
   envelope, STEP replay, Scene parse, and independent review.

Complete shared dimensions and helper providers before editing their importers.
After a multi-file repair, reconcile every changed import and call site before
the next validation. Keep housing, frame, carrier, and belt support geometry as
simple as the requested function permits; standard tooth geometry already
dominates STEP and render cost. If validation times out, use its
reported `execution_phase`: reduce boolean detail for `model_build`, repair the
constraint graph for `strict_constraint_solve`, and simplify nonfunctional
mesh detail for export/review phases. A timeout retry must materially
reduce the named phase's cost.

## Scope of claims

Tooth geometry and kinematic coupling do not prove torque capacity, tooth
stress, contact ratio, bearing life, shaft deflection, backlash under load,
lubrication, efficiency, noise, tolerance stack, or thermal performance. Put
unperformed engineering analyses in `PRODUCT_SPEC.assumptions`. Preserve all
user-specified interfaces and dimensions while making non-critical assumptions
autonomously.
