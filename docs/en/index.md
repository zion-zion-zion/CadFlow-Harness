---
icon: lucide/box
---

# CadFlow Harness

CadFlow Harness is a local tool for running CAD agents. The agent writes an
executable Python program, CadFlow runs it with a deterministic geometry kernel,
and the application keeps the source, measurements, progress events, and
validated product artifacts together.

The generated result stays as Python source. You can inspect its dimensions and
features, edit the file, and run it again. Use the Viewer for previews and
accepted geometry.

!!! warning "Alpha, trusted local use"

    This release is not a hosted multi-tenant service or a security boundary for
    untrusted code. Generated code runs in bounded local processes. Run the app
    only on a machine you trust.

## Available now

- FastAPI Project and Run APIs with a same-origin Three.js Viewer.
- A Deep Agents harness that can create or revise `/code/model.py` and helper
  modules inside one Project.
- `Shape` single-part and semantic `Assembly` execution, product validation,
  Scene generation, and an independent CAD review.
- Live source previews, progress events, redacted trace downloads, and versioned
  artifacts for accepted results.
- Five public CAD Skills for parts, flexible geometry, STEP/BREP, assemblies,
  and rotary transmission.

## Start here

- [Quick start](quickstart.md) installs the locked environment and starts the
  local app.
- [Create your first Project](first-project.md) creates a Project and submits a
  first task.
- [Example catalog](examples/index.md) links to runnable repository examples.
- [Architecture](architecture.md) describes the Agent, executor, kernel, and
  Viewer boundaries.
- [Configuration](reference/configuration.md) lists environment variables and
  storage paths.

The [GitHub repository](https://github.com/zion-zion-zion/CadFlowAgent) contains
the source, tests, Skills, examples, and issue list.

## Project status

The runtime and its run records are implemented in this repository. A small
reconstruction-data example is included. Large-scale dataset collection,
evaluation infrastructure, and CAD-specific post-training are not implemented
and are not capabilities of the released model.
