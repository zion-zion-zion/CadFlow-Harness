---
icon: lucide/box
---

# CadFlow Harness

CadFlow Harness is a local, auditable runtime for agents that write and verify
parametric CAD programs. A model writes executable Python, CadFlow executes it
with a deterministic geometry kernel, and the application keeps the source,
measurements, progress events, and validated product artifacts together.

It is a programming and verification loop, not a 3D model that emits a mesh or
an image in one opaque step. The generated Python remains readable and
editable, while geometric checks provide evidence beyond visual similarity.

!!! warning "Alpha, trusted-local software"

    The current release is intended for a trusted local demonstration. It runs
    generated code in bounded local processes and is not a hosted multi-tenant
    service or a security boundary for untrusted users.

## What is available now

- A FastAPI Project and Run API with a same-origin Three.js Viewer.
- A Deep Agents harness that can create or revise `/code/model.py` and helper
  modules in one Project workspace.
- CadFlow `Shape` and semantic `Assembly` execution, product validation, Scene
  generation, and independent CAD review before acceptance.
- Live source previews, progress events, redacted trace downloads, and
  versioned artifacts for accepted results.
- Five public CAD Skills covering parts, flexible geometry, STEP/BREP
  inspection, assemblies, and rotary transmission design.

## Choose a path

- [Quick start](quickstart.md) installs the locked environment and starts the
  local app.
- [First Project](first-project.md) walks through creating a Project and
  submitting a first task.
- [Example catalog](examples/index.md) points to runnable repository examples.
- [Architecture](architecture.md) explains the Agent, executor, kernel, and
  Viewer boundaries.
- [Configuration reference](reference/configuration.md) lists effective
  environment variables and paths.

The [GitHub repository](https://github.com/zion-zion-zion/CadFlowAgent) contains
the source, tests, Skills, examples, and issue tracker.

## Project direction

The runtime and its observable trajectories are implemented today. A small
reconstruction-data example is included in the repository, but large-scale
dataset curation, evaluation infrastructure, and CAD-specific post-training
remain future work. They should not be read as shipped model capabilities.
