# CadFlowAgent

CadFlowAgent is a trusted local Text-to-CAD workspace. A user creates one
Project, submits one complete part or assembly description, and a bounded Deep Agent writes
and validates a CadFlow Python Model Source before the Viewer loads its
canonical Scene Artifact.

## Requirements

- Linux x86_64 with glibc 2.31 or newer and Python 3.12, or macOS 26 arm64
  with Python 3.13
- `curl` or `wget` for installing missing tools
- API settings copied from `.env.example` to `.env`

The repository vendors one CadFlow wheel for each supported platform. `uv`
selects the matching wheel from the shared `pyproject.toml` and `uv.lock`:

- Linux: `cadflow-0.1.0-cp312-cp312-linux_x86_64.whl` (SHA256
  `753c513fee879258a561efa9d3edf7e73ebe904ed160264caf5851c20b99854f`)
- macOS: `cadflow-0.1.0-cp313-cp313-macosx_26_0_arm64.whl` (SHA256
  `738bcccab01a8152831a871f3103790feb4d975c1e98357b887fc4ebe56391fa`)

Run the setup script once after cloning. It detects the platform, installs
`uv` and a compatible Node.js version when needed, syncs the locked Python and
Viewer dependencies, and creates `.env` from the example without overwriting an
existing file.

```bash
./setup.sh
```

Use `./setup.sh --check` to validate an existing environment without changing
it.

## Run

```bash
./run.sh
```

`run.sh` detects the operating system and architecture and selects the matching
Python interpreter automatically.

The backend listens on `127.0.0.1:8765` by default and the Vite viewer on
`127.0.0.1:5678`. `TEXT_TO_CAD_HOST` may be set when the local viewer needs to
reach the backend from another machine; this remains a trusted-demo boundary.

## Skills and Model Source

The Agent discovers the `SKILL.md` workflows under `skills/`, selects the ones
whose descriptions match the request, and reads their full instructions only
when needed. The skill files are the source of truth for CadFlow workflows and
API references.

Each Project starts with a non-passing `model.py` scaffold and keeps this stable
entry point for both parts and assemblies:

```python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape | cad.Assembly:
    # The Agent replaces this scaffold with the requested model.
    raise NotImplementedError
```

The Agent owns the Project workspace and may add local modules or use any
already-installed local CadFlow/Python API. A single-part request must return
one valid `cad.Shape` with exactly one positive-volume solid. A multi-part
request must return a `cad.Assembly` whose leaf Parts each contain one
positive-volume `cad.Solid`; multi-solid Shapes and flattened Compounds are not
accepted as final results. The backend produces
`artifacts/model.scene.zip`, uses the same Scene package for live preview, and
records whether the validated result is a part or assembly. Review evidence
hashes every local Python source file so optional helper modules are covered by
the same validation and version history as `model.py`.

Agent runs may read repository Skills as a read-only reference, but repository
examples are not mounted into the Agent workspace. The root `model.py` remains
the required entry point; helper modules are optional and should follow real
component or shared-dimension boundaries rather than a fixed file-count rule.

## Checks

```bash
# Linux
uv run --python 3.12 pytest

# macOS
uv run --python 3.13 pytest

cd viewer && npm run build
```

Live provider tests are opt-in with `-m live_agent`.
