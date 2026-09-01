# Project status and limits

## Implemented

- A local FastAPI backend and Vite/Three.js browser workspace.
- A persistent catalog for multiple Projects, with one active Run per Project.
- A Deep Agents harness with read-only Skills and a restricted Python source
  workspace.
- `cad.Shape` single-part and semantic `cad.Assembly` product execution.
- Geometry, product, Scene, STEP replay, and independent review checks, followed
  by versioned Accepted artifacts when the checks pass.
- Live GLB previews, SSE progress, trace inspection, redacted downloads, and
  product downloads.

## Bounded behavior

- The flexible SDK and Assembly API are available in examples and Skills, but
  some examples do not match the Agent's current return contract.
- Live previews are for inspection. They may lag, fail, or be unavailable; the
  final validation decides whether a result is Accepted.
- Reasoning modes and the optional review model depend on the configured
  OpenAI-compatible endpoint.
- The application targets a trusted local machine and runs generated code in
  bounded local processes.

## Planned work

Large-scale reconstruction datasets, benchmark and evaluation pipelines, and
CAD-specific post-training are not implemented. The trajectory example is not a
released dataset or a trained model.

## Contract limits

- A Part result must be one valid positive-volume solid.
- An Assembly must preserve semantic Part boundaries. Fusing separate parts into
  one Shape is not an Assembly substitute.
- One Project cannot run two Agent turns concurrently.
- Prompts are limited to 32,000 characters.
- Accepted artifact retention defaults to ten versions per Project.
- Shape/Assembly validity does not prove mechanical strength, tolerances,
  manufacturability, thermal behavior, or other analyses not implemented here.
