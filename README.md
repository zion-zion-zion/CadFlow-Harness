<p align="center">
  <img src="docs/assets/cadflow-harness-logo.png" width="168" alt="CadFlow Harness logo">
</p>

<h1 align="center">CadFlow Harness</h1>

<p align="center">
  <strong>Run CAD agents locally and inspect the geometry they produce.</strong>
</p>

<p align="center">
  The agent writes Python CAD programs. CadFlow runs and checks them, while each
  Project keeps its source, measurements, and run history.
</p>
<p align="center">
  <sub>Developed by</sub><br>
  <img src="docs/assets/coserve-ai-logo.png" width="220" alt="Coserve AI">
</p>

<p align="center">
  <a href="https://www.python.org/downloads/release/python-3120/"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"></a>
  <a href="https://nodejs.org/"><img alt="Node.js 22.19+" src="https://img.shields.io/badge/Node.js-22.19+-5FA04E?logo=nodedotjs&logoColor=white"></a>
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/License-MIT-22C55E"></a>
  <img alt="Platform Linux x86-64" src="https://img.shields.io/badge/Platform-Linux%20x86--64-FCC624?logo=linux&logoColor=black">
  <a href="http://119.28.82.252/cadflow-harness/"><img alt="Documentation" src="https://img.shields.io/badge/Docs-Online-2563EB?logo=readthedocs&logoColor=white"></a>
  <a href="https://github.com/yhz5613813/CadFlow"><img alt="CadFlow repository" src="https://img.shields.io/badge/CadFlow-GitHub-181717?logo=github&logoColor=white"></a>
  <img alt="Status Alpha" src="https://img.shields.io/badge/Status-Alpha-F59E0B">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#core-design">Core design</a> ·
  <a href="#project-direction">Project direction</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#cad-skill-layer">CAD Skills</a>
</p>

---

CadFlow Harness is a local runtime for parametric CAD agents. A model writes an
executable Python program, CadFlow runs it with a deterministic geometry kernel,
and the backend stores the measurements, events, and product files from that
run.

The model's output is a program rather than a finished mesh or image. You can
read the source, change its dimensions or features, run it again, and compare
the resulting geometry. The repository contains the runtime, examples, Skills,
and tests; it does not ship a specialized model.

> [!IMPORTANT]
> This is an alpha release for trusted local use. A single-part result must be
> one valid solid with positive volume. An Assembly keeps separate Part
> boundaries, placements, connectors, and constraints. Large-scale datasets and
> CAD-specific post-training are planned work, not bundled capabilities.

## Documentation

- [Live documentation (Chinese default)](http://119.28.82.252/cadflow-harness/)
- [CADTestBench smoke benchmark](benchmark/README.md)

<a id="core-design"></a>

## Core design

### Python is the model output

The agent produces readable Python CAD source. Dimensions and feature order stay
in the file, so a person can inspect or edit them after a run.

### Geometry checks the result

CadFlow builds the geometry and the runtime checks it. The checks cover solid
count, volume, bounds, topology, sections, and BREP or material comparisons.
They provide measurements that a screenshot cannot.

### Runs leave a record

Each turn stores the prompt, tool calls, source revisions, execution result,
measurements, and generated files. The [reconstruction dataset example](examples/agentic_reconstruction_dataset/README.md)
shows one way to package these records without saving hidden chain-of-thought.

### The model layer can change

The Deep Agents harness uses the same Project workspace and artifact contract as
the rest of the runtime. A different model or harness can be connected without
rewriting the executor, checks, run history, or Viewer.

<a id="project-direction"></a>

## Project direction

| Area | What exists | Next work |
| --- | --- | --- |
| Agent runtime | Write, run, inspect, and repair CadFlow programs in the local workspace. | Available now |
| Dataset and evaluation | Reproducible five-sample CADTestBench smoke benchmark. | Scale collection and evaluation |
| CAD post-training | No training pipeline in this repository. | Research and prototype |

The runtime is the part you can use today. The dataset and training items in the
table describe planned work.

<a id="architecture"></a>

## Architecture

<p align="center">
  <img src="docs/assets/cadflow-harness-architecture.svg" alt="CadFlow Harness system architecture diagram">
</p>

### One modeling run

1. Create a Project and submit a complete CAD task.
2. The selected harness reads the relevant Skills and writes `code/model.py`.
3. CadFlow executes the program. Geometry checks return measurements when it
   fails or misses a requirement.
4. A passing run produces `model.scene.zip` for the Viewer. The source, events,
   diagnostics, and other artifacts remain in the Project for inspection.

<a id="quick-start"></a>

## Quick start

### Requirements

- Linux x86_64 with glibc 2.31 or newer and Python 3.12, or macOS 26 arm64 with Python 3.13
- `curl` or `wget` for installing missing tools
- An OpenAI-compatible model endpoint and API key

The repository includes a CadFlow wheel for each supported platform. `uv` picks
the matching wheel from `pyproject.toml` and `uv.lock`.

### Install

~~~bash
./setup.sh
~~~

The setup script detects the platform, installs missing `uv` or Node.js tools,
syncs the locked Python and Viewer dependencies, and creates `.env` from the
example without replacing an existing file. Run `./setup.sh --check` to inspect
an existing setup without changing it.

Set the model provider in `.env`:

~~~dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=<model-id>
OPENAI_API_KEY=<api-key>
~~~

### Launch

~~~bash
./run.sh
~~~

`run.sh` selects the Python interpreter for the current operating system and
architecture. Open [http://localhost:5678](http://localhost:5678) to create a
Project, submit a task, watch its progress, and inspect the final result. The
backend API listens on `http://localhost:8765`.

### Run the benchmark

With a real OpenAI-compatible model configured in `.env`, run the fixed
CADTestBench smoke suite:

~~~bash
cd benchmark
./run.sh --suite smoke-5
~~~

See [benchmark/README.md](benchmark/README.md) for the dataset revision,
evaluation contract, reproducibility details, and result metrics.

### Runtime configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_BASE_URL` | Not set | Base URL for the OpenAI-compatible provider. |
| `OPENAI_MODEL_ID` | Not set | Model used by the agent harness. |
| `OPENAI_API_KEY` | Not set | Provider credential. Never commit it. |
| `TEXT_TO_CAD_HOST` | `0.0.0.0` | Backend bind host. |
| `TEXT_TO_CAD_PORT` | `8765` | Backend port. |
| `TEXT_TO_CAD_FRONTEND_HOST` | `0.0.0.0` | Viewer bind host. |
| `TEXT_TO_CAD_FRONTEND_PORT` | `5678` | Viewer bind port. |
| `TEXT_TO_CAD_PROJECTS_ROOT` | `output/projects` | Project workspace root. |
| `CADFLOW_PREVIEW_TIMEOUT_SECONDS` | `15` | Live preview worker timeout in seconds. |

## Current modeling contract

Each Project starts with this entry point in `code/model.py`:

~~~python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape | cad.Assembly:
    # The agent replaces this scaffold with the requested product.
    raise NotImplementedError
~~~

The Agent can read and write Python in the Project's `code/` workspace. A
single-part result must be one valid `cad.Shape` with one solid and positive
volume. An Assembly keeps semantic Part boundaries, placements, connectors, and
constraints. The backend validates the result and creates
`artifacts/model.scene.zip` for the Viewer. Generated code runs in bounded local
processes, so use the application only on a trusted machine.

<a id="cad-skill-layer"></a>

## CAD Skill layer

Skills are task-specific Markdown references loaded read-only for an Agent Run.

| Skill | Focus |
| --- | --- |
| [`cadflow-model-part`](skills/cadflow-model-part/SKILL.md) | Parametric rigid parts, sketches, features, booleans, blends, and single-part delivery. |
| [`cadflow-flexible-model`](skills/cadflow-flexible-model/SKILL.md) | Static cloth, leather, membranes, garments, and other flexible geometry. |
| [`cadflow-step-brep`](skills/cadflow-step-brep/SKILL.md) | STEP/BREP inspection, feature inference, reconstruction, and evidence-based comparison. |
| [`cadflow-model-assembly`](skills/cadflow-model-assembly/SKILL.md) | Multi-part products, placements, connectors, constraints, and acceptance. |
| [`cadflow-rotary-transmission`](skills/cadflow-rotary-transmission/SKILL.md) | Rotary joints, gears, shafts, housings, and transmission mechanisms. |

## Repository map

~~~text
CadFlow Harness/
├── backend/          Agent runtime, Project API, execution, events, and validation
├── viewer/           Browser workspace and Three.js Scene viewer
├── skills/           CadFlow workflows and API references
├── examples/         Parts, flexible models, assemblies, and reconstruction data
├── tests/            Runtime, boundary, repair-loop, and integration tests
└── vendor/           Platform-specific CadFlow wheels
~~~

The [example catalog](examples/README.md) lists runnable mechanical parts,
assemblies, flexible geometry, and the reconstruction-record seed.

## Checks

~~~bash
# Linux
uv run --locked --python 3.12 pytest

# macOS
uv run --locked --python 3.13 pytest

cd viewer && npm run build
cd .. && ./scripts/docs.sh build
~~~

Live provider tests are opt-in with `-m live_agent`.

## License

CadFlow Harness is licensed under the [MIT License](LICENSE).
