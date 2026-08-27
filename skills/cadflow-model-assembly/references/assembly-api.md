# CadFlow Assembly API

Use these public functions inside an active CadFlow model session. All
assembly mutators return a new value; retain the returned `Assembly`.

## Geometry API boundary

Assembly Part bodies use the replayable geometry family from primitive through
boolean:

```python
plate = cad.make_box_rsolid(
    width=40.0,
    height=24.0,
    depth=4.0,
    bottom_face_center=(0.0, 0.0, 0.0),
)
boss = cad.make_cylinder_rsolid(
    radius=6.0,
    height=8.0,
    bottom_face_center=(0.0, 0.0, 0.0),
    axis=(0.0, 0.0, 1.0),
)
body = cad.union_rsolid(plate, boss)
assert isinstance(body, cad.Solid)
```

`make_box_rsolid` and `make_cylinder_rsolid` return `cad.Solid`.
`union_rsolid` and `cut_rsolid` consume replayable solids; every intended union
member must overlap so the result stays one connected solid. Position these
primitives with `bottom_face_center` and orient cylinders with `axis`. Keep the
definition in a coherent local frame, then position each occurrence with its
component `placement`.

Diagnose replayable Part placement from shared dimensions, constructor
arguments, connector frames, and structured `validate_model` results. The
documented `cad.Solid` workflow here provides `get_volume()` but no
`bbox`/`describe` introspection method; derive primitive axial and radial
intervals in the dimension module instead of guessing extra methods.

The `cad.Model` DSL is the separate single-Part frontend: `model.box(...)` and
`model.translate(shape, x, y, z)` return `cad.Shape`. A frontend Shape is not a
valid `make_part_rpart(..., body=...)` body and cannot enter replayable solid
booleans. For an Assembly Part, stay in the replayable family; use component
placements rather than `model.translate` to position occurrences.

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

Couple the components that own the established joint degrees of freedom. For
example, when a gear is fixed to a revolute shaft, reference the shaft's axis
connector in the gear constraint rather than a connector on the fixed gear
Part. Add the revolute/prismatic joints before their couplings. Leave
`phase_offset=None` to capture the current joint pose unless the design has an
explicit phase equation.

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
assert all(item.within_tolerance for item in report.residuals)
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

The runtime performs this strict solve and residual check on the returned
Assembly even when `build_model` returns the authored, unsolved value. Solving
inside source can still be useful while constructing or diagnosing a large
constraint graph.

Inspect residuals rather than relying only on `report.solved`. When driving a
motion pose, rebuild or update the relevant driven constraint through supported
public functions, solve again, then rerun envelope and collision checks.

## Runtime entry and product contract

```python
import cadflow as cad

from assembly import make_drive_module_rassembly
from dimensions import validate_design_dimensions

PRODUCT_SPEC = {
    "assumptions": ["Rated-load calculations require separate analysis."],
    "envelope": {"max_size_mm": [80.0, 80.0, 120.0]},
    "collision_exclusions": [],
}

def build_model(model: cad.Model) -> cad.Assembly:
    validate_design_dimensions()
    return make_drive_module_rassembly()
```

Keep `PRODUCT_SPEC` available in `model.py`'s global namespace. Its values must
be JSON-compatible. The executor owns strict solving, flattened Scene
projection, semantic serialization, STEP export and replay, unique-Part STEP
export, BOM, validation, assumptions, and the complete source snapshot.

## Automatic collision verification

The executor checks every current-pose leaf pair at 0.02 mm maximum
penetration. For one intentional contact or interference fit, declare the
single excluded pair in `PRODUCT_SPEC`:

```python
PRODUCT_SPEC["collision_exclusions"] = [
    {
        "component_a": "drive_module/housing",
        "component_b": "drive_module/press_fit_ring",
        "reason": "Specified diametral press fit",
    }
]
```

Paths may include the root Assembly ID; nested paths include every component
segment. Validation reports normalized paths when an entry is wrong. An
exclusion skips the whole pair, so it must be unique, pair-specific, and
physically justified. The verifier checks mesh penetration only at the current
pose. It does not sweep motion or reliably detect complete containment without
surface contact.
