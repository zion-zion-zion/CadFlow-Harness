# Use CadFlow native Model/Shape sources

The CadFlow migration keeps the existing Deep Agent, Project, execution, and
Viewer architecture while changing the generated Python boundary. Every new
Model Source defines `build_model(model: cad.Model) -> cad.Shape` and uses only
CadFlow's documented `Model`/`Shape` API. The Agent must not use CadFlow's
compatibility decorators, legacy `*_rsolid` operations, private implementation
modules, or direct OCP imports.

The executor owns the `cad.Model` lifetime and validates one returned Shape with
finite positive volume and exactly one solid. CadFlow 0.1.0's native frontend
does not expose a native-Shape Scene export, so the executor creates the
canonical Scene ZIP internally through the public STEP inspection, `Solid`,
`compile_scene`, and `export_scene` APIs. This bridge is an implementation
detail and is not part of the generated source contract.
