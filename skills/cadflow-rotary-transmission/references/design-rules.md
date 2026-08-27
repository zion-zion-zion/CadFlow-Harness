# Rotary Transmission Design Rules

Use one sign convention and one unit system throughout. The formulas below are
pitch-geometry and ideal kinematic checks; they are not strength calculations.

## External cylindrical gear pair

For module `m` and tooth counts `z1`, `z2`:

```text
r1 = m * z1 / 2
r2 = m * z2 / 2
center_distance = r1 + r2
speed_2 / speed_1 = -z1 / z2
ratio_magnitude = z2 / z1
```

Both gears use the same module and pressure angle. A helical or herringbone
pair also needs compatible helix handedness. Use the pitch radii above for the
CadFlow gear-coupling constraint, matching the standard gear constructors.

## Internal gear pair and simple planetary stage

For sun, planet, and ring tooth counts:

```text
z_ring = z_sun + 2 * z_planet
r_planet_center = m * (z_sun + z_planet) / 2
internal_center_distance = r_ring - r_planet
```

For an equally spaced `planet_count`, require the assembly condition
`(z_sun + z_ring) % planet_count == 0` unless a deliberate unequal phase is
modeled. Place planet centers at
`2*pi*index/planet_count` around the carrier. Reuse one planet Part definition.

Coaxial input and output shafts still need distinct material domains. Give two
solid shafts disjoint axial intervals with a real gap, or model one as a hollow
shaft with radial clearance around the other. Assert the chosen interval or
radial-clearance condition in shared dimensions before building the Parts.

For a fixed ring, sun input, carrier output stage, the ideal reduction is:

```text
ratio = 1 + z_ring / z_sun
```

External sun/planet meshes rotate oppositely and use gear coupling. The
internal ring/planet relation rotates the same direction at the contact
abstraction and can use belt coupling with ring and planet pitch radii. The
couplings encode kinematics only; the standard gear solids encode teeth.

## Compound stages

Multiply ideal stage ratios, including sign, to obtain overall input/output
speed relation. A rigid compound shaft or carrier links adjacent stages through
fixed constraints. Check axial stack length, bearing/support span, and housing
clearance independently from the ratio equation.

## Belt or pulley stage

Use pitch radii `r_driver`, `r_driven`:

```text
speed_driven / speed_driver = r_driver / r_driven
```

An open belt preserves rotation direction and uses belt coupling. A crossed
belt reverses direction; model that intent explicitly rather than relying on
the default same-direction coupling. Belt coupling does not create a belt
solid, tooth engagement, wrap angle, or tension analysis.

## Right-angle straight bevel pair

For a 90-degree shaft angle and tooth counts `z1`, `z2`:

```text
pitch_angle_1 = atan(z1 / z2)
pitch_angle_2 = 90 degrees - pitch_angle_1
ratio_magnitude = z2 / z1
```

Keep a common cone-apex datum for gear geometry and occurrence placement. After
placement, both gear pitch cones must reference that same world point while
their axes remain aligned with their shafts. This apex datum is not a revolute
joint or the connector pair used by CadFlow's kinematic coupling.

Model reusable shaft Parts in one local frame, normally with the solid and all
axis connectors along local `+Z`. The component placement alone maps that local
axis onto world `X` or `Y`; do not encode the eventual world axis inside the
Part connector.

Give each shaft exactly one physical support revolute and leave it undriven
with `drive_angle_degrees=None`; the coupling needs both rotational DOFs. Use
the two shaft connectors that already participate in those revolute joints in
`add_gear_constraint_rassembly(..., phase_offset=None)`. Their support origins
may differ: the gear constraint couples the established joint coordinates, not
the physical tooth-contact point. Adding a second revolute at the apex or
coupling through detached apex connectors overconstrains the solver. The apex
may lie outside the shaft solid. Set each perpendicular shaft body's
axial interval back from the apex so it enters only its own gear/hub region and
does not cross the other shaft or gear solid. Before building, record each
gear's addendum or measured body extent projected onto the perpendicular shaft
axis. Require

```text
shaft_start > opposite_gear_axis_extent + collision_clearance
own_hub_end >= shaft_start + required_engagement_length
```

For a standard full-depth first estimate,
`opposite_gear_axis_extent = m * (z_opposite + 2) / 2`; the generated body's
actual bound is authoritative. Extend the bored coaxial hub when needed so the
shaft still has positive engagement with its own gear. Encode both the
clearance and engagement inequalities in shared dimensions. Repair a reported
shaft/gear pair rather than excluding it, then validate the coupling and these
collision pairs before adding secondary detail. The ideal ratio alone does not
prove bevel tooth contact, so also validate the actual bounds and current pose.

## Invariants to encode

- positive finite module, face width, gear height, clearances, and radii;
- integer tooth counts supported by the chosen standard constructor;
- identical mesh parameters on mating gears;
- every bore smaller than the remaining root/hub region;
- requested overall ratio within the tolerance stated by the user, or record a
  clearly named integer-tooth approximation assumption;
- housing inner clearance outside rotating addendum bounds and enough material
  outside bores, bearing seats, and mounting holes;
- axial spans fit the declared product envelope; and
- each repeated angular pattern uses its declared count, never a hard-coded
  divisor.
