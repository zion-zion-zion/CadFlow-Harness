# Project status and limits

## Implemented

- Trusted-local FastAPI backend and Vite/Three.js browser workspace.
- Durable multi-Project catalog with one active Run per Project.
- Deep Agents harness with read-only Skills and a bounded Python source
  workspace.
- `cad.Shape` single-part and semantic `cad.Assembly` product execution.
- Deterministic geometry, product, Scene, STEP replay, and independent review
  gates, followed by versioned Accepted artifacts.
- Live GLB previews, SSE progress, bounded traces, redaction, and product
  downloads.

## Experimental or bounded

- The flexible SDK and Assembly APIs are available in examples and Skills, but
  not every example is compatible with the Agent's current return contract.
- Live previews are best effort and may lag, fail, or be unavailable while the
  accepted result remains valid.
- Provider reasoning modes and optional review models depend on the configured
  OpenAI-compatible endpoint.
- The application is designed for a trusted local machine; generated code is
  executed in local bounded processes.

## Future direction

Large-scale reconstruction datasets, benchmark/evaluation pipelines, and
CAD-specific post-training are project directions. The repository's seed
trajectory example does not represent a shipped dataset or trained model.

## Explicit contract limits

- A Part result must be one valid positive-volume solid.
- An Assembly must preserve semantic Part boundaries; fusing separate parts into
  one Shape is not an Assembly substitute.
- One Project cannot run two Agent turns concurrently.
- Prompts are limited to 32,000 characters.
- Accepted artifact retention defaults to ten versions per Project.
- Shape/Assembly validity does not prove mechanical strength, tolerances,
  manufacturability, thermal behavior, or other analyses not implemented here.
