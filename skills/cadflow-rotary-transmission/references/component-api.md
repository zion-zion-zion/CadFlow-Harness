# CadFlow Rotary Component API

These are public constructors available in the bundled CadFlow SDK. Call them
inside the active model session and wrap each resulting one-solid gear in a
semantic `cad.Part`.

## Cylindrical gears

```python
spur = cad.std.gear.make_spur_gear_rsolid(
    n_teeth=24,
    module=1.0,
    pressure_angle=20.0,
    gear_height=6.0,
    backlash=0.05,
)

helical = cad.std.gear.make_helical_gear_rsolid(
    n_teeth=24,
    module=1.0,
    pressure_angle=20.0,
    helix_angle=25.0,
    gear_height=8.0,
    backlash=0.05,
)

herringbone = cad.std.gear.make_herringbone_gear_rsolid(
    n_teeth=24,
    module=1.0,
    pressure_angle=20.0,
    helix_angle=30.0,
    gear_height=10.0,
    backlash=0.05,
)
```

All three accept keyword-only `addendum_factor` and `clearance_factor`.

Internal forms are:

- `make_spur_ring_gear_rsolid(..., gear_height=6.0,
  rim_thickness=3.0, backlash=0.0)`
- `make_helical_ring_gear_rsolid(..., helix_angle=25.0,
  gear_height=8.0, rim_thickness=3.0, backlash=0.0)`
- `make_herringbone_ring_gear_rsolid(..., helix_angle=30.0,
  gear_height=10.0, rim_thickness=3.0, backlash=0.0)`

They take the same required `n_teeth`, `module`, and `pressure_angle` values.
Build shaft or bearing bores with a coaxial cylinder cutter and validate the
remaining gear solid before wrapping it as a Part.

## Straight bevel gear

```python
bevel = cad.std.gear.make_straight_bevel_gear_rsolid(
    n_teeth=20,
    module=1.0,
    pitch_angle=45.0,
    pressure_angle=20.0,
    face_width=8.0,
    backlash=0.05,
)
```

This constructor also accepts keyword-only `addendum_factor` and
`clearance_factor`.

## Ball bearing

```python
bearing = cad.std.bearing.make_ball_bearing_rassembly(
    bore_diameter=8.0,
    outer_diameter=22.0,
    bearing_width=7.0,
    ball_diameter=3.0,
    ball_count=8,
    raceway_clearance=0.03,
    edge_chamfer=0.2,
    assembly_id="bearing_608_style",
)
```

The result is a reusable nested Assembly, not a Part. Use its public
`outer_axis` and `inner_axis` connectors for housing and shaft interfaces.
Reuse one definition under distinct parent component IDs when dimensions are
identical. A plain bushing may instead be one annular Part when that matches the
requested product.

## Shafts, hubs, and housings

Use public primitives and booleans such as `make_cylinder_rsolid`,
`make_box_rsolid`, `union_rsolid`, and `cut_rsolid`. Build functional bores,
seats, flanges, mounting holes, and covers rather than representing them only
with labels. Keep a separately manufactured shaft, gear, carrier, housing, and
cover as distinct Parts even when their current-pose solids touch.

These replayable constructors, their booleans, and the `cad.Model` Shape DSL
are different geometry families. Follow the Geometry API boundary in the
Assembly API reference. Build each rotary Part in one local frame, keep every
union connected, and position its occurrence through Assembly placement.
