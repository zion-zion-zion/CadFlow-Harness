# CadFlow Assembly API

Use these public functions inside an active CadFlow model session. All
assembly mutators return a new value; retain the returned `Assembly`.

## Parts and connectors

```python
part = cad.make_part_rpart(part_id="shaft", body=shaft_solid, name="Input shaft")
part = cad.add_connector_rpart(
    part=part,
    connector=cad.make_placement_connector_rconnector(
        connector_id="axis",
        placement=cad.make_placement_rplacement(origin=(0.0, 0.0, 0.0)),
        name="Shaft axis",
    ),
)
```

`make_part_rpart(part_id, body, name=None)` requires exactly one `cad.Solid`.
Use `make_face_connector_rconnector(connector_id, face, name=None, flip=False)`
when a datum must be anchored to selected BREP geometry. Use
`make_placement_connector_rconnector(connector_id, placement, name=None)` for
an explicit local frame. `make_placement_rplacement(origin, x_axis, y_axis)`
constructs a canonical right-handed placement.

## Product structure

```python
assembly = cad.make_assembly_rassembly(
    assembly_id="drive_module",
    name="Drive module",
)
assembly = cad.add_component_rassembly(
    assembly=assembly,
    item=housing_part,
    component_id="housing",
    placement=cad.identity_placement_rplacement(),
    name="Fixed housing",
)
assembly = cad.add_component_rassembly(
    assembly=assembly,
    item=shaft_part,
    component_id="input_shaft",
    placement=cad.identity_placement_rplacement(),
    name="Input shaft",
)
```

`item` may be a `cad.Part` or nested `cad.Assembly`. Component IDs are unique
within their parent. Reuse the same Part/subassembly definition for repeated
instances.

Expose a stable parent interface without leaking the child hierarchy:

```python
assembly = cad.forward_connector_rassembly(
    assembly=assembly,
    connector_id="input_axis",
    source_component_id="input_shaft",
    source_connector_id="axis",
    name="Module input axis",
    offset=None,
)
```

## Constraint references and joints

```python
def ref(component_id: str, connector_id: str) -> cad.ConnectorRef:
    return cad.make_connector_ref_rconnectorref(
        component_id=component_id,
        connector_id=connector_id,
    )

assembly = cad.ground_component_rassembly(
    assembly=assembly,
    component_id="housing",
)
assembly = cad.add_revolute_constraint_rassembly(
    assembly=assembly,
    constraint_id="shaft_revolute",
    connector_a=ref("housing", "bearing_axis"),
    connector_b=ref("input_shaft", "axis"),
    drive_angle_degrees=0.0,
    angle_limit=None,
    name="Input shaft rotation",
)
```

Core joint functions are:

- `add_fixed_constraint_rassembly(..., connector_a, connector_b, name=None)`
- `add_revolute_constraint_rassembly(..., drive_angle_degrees=None,
  angle_limit=None, name=None)`
- `add_prismatic_constraint_rassembly(..., drive_distance=None,
  distance_limit=None, name=None)`

Create closed revolute/prismatic limits with
`make_scalar_limit_rscalarlimit(lower_value, upper_value)`. A fixed constraint
coincides complete frames; revolute and prismatic constraints preserve their
respective degree of freedom unless driven.

Motion couplings are:

- `add_gear_constraint_rassembly(..., pitch_radius_a, pitch_radius_b,
  phase_offset=None, name=None)` for inverse rotation.
- `add_belt_constraint_rassembly(..., pulley_radius_a, pulley_radius_b,
  phase_offset=None, name=None)` for same-direction rotation.
- `add_rack_pinion_constraint_rassembly(..., rack_connector,
  pinion_connector, pitch_radius, phase_offset=None, name=None)`.

Couplings represent kinematics between joint axes. They do not construct gear
teeth, establish physical contact, or calculate backlash and load capacity.

## Solve and inspect

```python
assembly = cad.solve_assembly_constraints_rassembly(
    assembly=assembly,
    strict=True,
)
report = cad.inspect_assembly_constraints_rconstraintreport(assembly=assembly)

worst_translation = max(
    (item.translation_error for item in report.residuals),
    default=0.0,
)
worst_angle = max(
    (item.angular_error_degrees for item in report.residuals),
    default=0.0,
)
assert report.solved
assert not report.unsolved_component_ids
print(
    "assembly",
    assembly.assembly_id,
    "components",
    len(assembly.component_ids()),
    "constraints",
    len(assembly.constraint_ids()),
    "max_translation",
    worst_translation,
    "max_angle",
    worst_angle,
)
```

Inspect residuals rather than relying only on `report.solved`. When driving a
motion pose, rebuild or update the relevant driven constraint through supported
public functions, solve again, then rerun envelope and collision checks.

## Replayable build and exports

```python
from pathlib import Path
import cadflow as cad

OUT = Path("outputs/drive_module")

@cad.model(graph_id="drive_module")
def build_drive_module():
    validate_design_dimensions()
    assembly = make_drive_module_rassembly()
    preview = cad.make_compound_from_assembly_rcompound(assembly=assembly)
    cad.capture_result(value=(assembly, preview))
    return assembly, preview

result = build_drive_module()
assembly, preview = result.value
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "drive_module.model.json").write_text(result.model_json, encoding="utf-8")
(OUT / "drive_module.session.json").write_text(result.session_json, encoding="utf-8")
cad.export_step(shapes=preview, filename=str(OUT / "drive_module.step"))
```

The `Compound` is a flattened geometry projection for inspection/export. Keep
the `Assembly` as the semantic source of component IDs, connectors, materials,
constraints, and motion relationships. Replay model JSON independently when
reproducibility is a requested deliverable.

## Static collision verification

```python
report = cad.verifier.check_collision_rcollisionreport(
    assembly=assembly,
    config=cad.verifier.CollisionCheckConfig(
        max_allowed_penetration=0.02,
        scope=cad.verifier.CollisionScope(
            exclude_pairs=(
                cad.verifier.ComponentPair("housing", "press_fit_ring"),
            ),
        ),
        max_contacts_per_pair=16,
    ),
)
assert report.completed
```

`CollisionScope` accepts `component_paths`, `include_pairs`, and
`exclude_pairs`; build included or excluded pairs with
`cad.verifier.ComponentPair(component_a, component_b)`. An exclusion skips the
whole pair, so keep it pair-specific and justify it from the interface
contract. The verifier checks FCL mesh penetration only at the
current pose. It does not solve constraints, sweep motion, or reliably detect
complete containment without surface contact. A useful report records checked
pair count, failed pair count, warnings, component paths, and penetration depth.
