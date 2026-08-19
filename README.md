# CadFlowAgent

CadFlowAgent is a trusted local Text-to-CAD workspace. A user creates one
Project, submits one complete part description, and a bounded Deep Agent writes
and validates a CadFlow Python Model Source before the Viewer loads its
canonical Scene Artifact.

## Requirements

- Linux x86_64
- Python 3.12
- `uv`, Node.js, and npm
- API settings copied from `.env.example` to `.env`

The repository vendors `vendor/cadflow-0.1.0-cp312-cp312-linux_x86_64.whl` (SHA256
`753c513fee879258a561efa9d3edf7e73ebe904ed160264caf5851c20b99854f`).

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

The current application still has a deliberately narrower output contract:
each Project starts with a non-passing `model.py` scaffold and keeps this stable
entry point:

```python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    # The Agent replaces this scaffold with the requested part.
    raise NotImplementedError
```

The Agent owns the Project workspace and may add local modules or use any
already-installed local CadFlow/Python API. The backend does not reject an API
based on its source. The returned value must still be one valid `cad.Shape` with
one solid and positive volume. The backend creates `artifacts/model.scene.zip`
after validating the returned Shape and keeps STEP conversion internal to the
Scene bridge. Agent runs may read repository Skills as a read-only reference,
but repository examples are not mounted into the Agent workspace. Their
alternative inputs or output types do not change this Project contract.

## Checks

```bash
uv run pytest
cd viewer && npm run build
```

Live provider tests are opt-in with `-m live_agent`.
