# CadFlowAgent

CadFlowAgent is a trusted local Text-to-CAD workspace. A user creates one
Project, submits one complete part description, and a bounded Deep Agent writes
and validates a CadFlow Python Model Source before the Viewer loads its
canonical Scene Artifact.

## Requirements

- Linux x86_64
- Python 3.12
- `uv` and Node.js/npm
- API settings copied from `.env.example` to `.env`

The repository vendors `vendor/cadflow-0.1.0-cp312-cp312-linux_x86_64.whl` (SHA256
`d48acda48f29f5c022695c377f7e0f6089c188923091fd45c3fd2c0e3234886a`).

```bash
uv sync --group dev
cd viewer && npm ci
```

## Run

```bash
./run.sh
```

The backend listens on `127.0.0.1:8000` by default and the Vite viewer on
`127.0.0.1:5173`. `TEXT_TO_CAD_HOST` may be set when the local viewer needs to
reach the backend from another machine; this remains a trusted-demo boundary.

## Skills and Model Source

The Agent discovers the `SKILL.md` workflows under `skills/`, selects the ones
whose descriptions match the request, and reads their full instructions only
when needed. The skill files are the source of truth for CadFlow workflows and
API references.

The current application still has a deliberately narrower execution contract:
each Project generates a single rigid-part `model.py` with this entry point:

```python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    return model.box(width=20.0, depth=30.0, height=10.0)
```

Generated code must use only CadFlow's documented `Model`/`Shape` API. The
backend creates `artifacts/model.scene.zip` after validating the returned Shape
and keeps STEP conversion internal to the Scene bridge. Skills and examples
that demonstrate flexible meshes, replayable graphs, or assemblies are SDK
references; they do not widen this Project contract.

## Checks

```bash
uv run pytest
cd viewer && npm run build
```

Live provider tests are opt-in with `-m live_agent`.
