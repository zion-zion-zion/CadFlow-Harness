<p align="center">
  <img src="docs/assets/cadflowagent-logo.png" width="168" alt="CadFlowAgent logo">
</p>

<h1 align="center">CadFlowAgent</h1>

<p align="center">
  <strong>Infrastructure for CAD agents that can build, verify, and learn from geometry.</strong>
</p>

<p align="center">
  Turn general-purpose language models into auditable CAD programmers—and each run<br>
  into a foundation for evaluation data, training trajectories, and better CAD agents.
</p>

<p align="center">
  <a href="https://www.python.org/downloads/release/python-3120/"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"></a>
  <a href="https://nodejs.org/"><img alt="Node.js 22.19+" src="https://img.shields.io/badge/Node.js-22.19+-5FA04E?logo=nodedotjs&logoColor=white"></a>
  <a href="LICENSE"><img alt="License AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-7C3AED"></a>
  <img alt="Platform Linux x86-64" src="https://img.shields.io/badge/Platform-Linux%20x86--64-FCC624?logo=linux&logoColor=black">
  <img alt="Status Alpha" src="https://img.shields.io/badge/Status-Alpha-F59E0B">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#core-design">🧭 Core design</a> ·
  <a href="#project-direction">🗺️ Project direction</a> ·
  <a href="#architecture">🏗️ Architecture</a> ·
  <a href="#quick-start">🚀 Quick start</a> ·
  <a href="#cad-skill-layer">🧠 CAD Skills</a>
</p>

---

CadFlowAgent is infrastructure for **running, verifying, and improving parametric CAD agents**. It lets a general-purpose language model write executable CAD programs, grounds the result in a deterministic geometry kernel, and preserves the observable run as evidence that can support evaluation and future post-training.

It is not a 3D foundation model that directly emits meshes or images. The model acts as a CAD programmer: it reads domain workflows, writes and revises Python source, calls CadFlow, and receives measurable geometric feedback. The primary asset is the reusable agent–CAD runtime around that process—not a fixed set of model weights.

> [!IMPORTANT]
> The current release is an alpha, trusted-local runtime focused on one valid solid per Project. Dataset scaling and CAD-specific post-training are the project direction, not features of a shipped specialized model.

<a id="core-design"></a>

## 🧭 Core design

### 🧩 Code is the generative medium

CadFlowAgent produces readable, editable, and replayable Python CAD source instead of an opaque polygon soup. Dimensions, feature order, and modeling intent remain available for inspection and iteration after generation.

### 📐 Geometry is the verifier

The same deterministic CAD kernel that builds a part can check whether it is valid. Solid count, volume, bounds, topology, sections, and BREP/material comparisons provide objective signals that are stronger than visual similarity alone.

### 🧾 Runs can become data

Prompts, observable tool activity, source revisions, execution outcomes, measurements, and artifacts form an auditable trajectory. The [agentic reconstruction dataset example](examples/agentic_reconstruction_dataset/README.md) demonstrates how CAD interaction records can be packaged without storing hidden chain-of-thought.

### 🔌 The intelligence layer is replaceable

The Deep Agents harness shares a stable Project workspace and artifact contract
with the rest of the runtime. Models, harnesses, and CAD Skills can evolve while
execution, verification, run records, and visualization stay reusable—this is
what makes CadFlowAgent infrastructure rather than a single-agent demo.

<a id="project-direction"></a>

## 🗺️ Project direction

| Stage | Goal | Status |
| --- | --- | --- |
| Agent runtime | Drive general-purpose models to write, execute, inspect, and repair single-part CadFlow programs in an interactive workspace. | ✅ Available now |
| Dataset and evaluation | Curate verified modeling and reconstruction trajectories, failures, repairs, geometric metrics, and final artifacts at scale. | 🌱 Seed example available; scaling is planned |
| CAD post-training | Use verified trajectories for supervised fine-tuning, preference learning, and geometry-grounded reward training. | 🧭 Roadmap |

The intended flywheel is simple: **better runtime → higher-quality trajectories → stronger evaluations and training data → more capable CAD agents**.

<a id="architecture"></a>

## 🏗️ Architecture

~~~mermaid
flowchart TB
    U["Natural-language CAD task"]

    subgraph L1["1 · Experience layer"]
        UI["Web workspace<br/>project control + 3D Viewer"]
        API["Project / Run API"]
        UI --> API
    end

    subgraph L2["2 · Agent layer"]
        C["Run Coordinator"]
        H["Agent Harness<br/>Deep Agents"]
        K["CAD Skills<br/>model · inspect · validate"]
        C --> H
        K --> H
    end

    subgraph L3["3 · Deterministic CAD layer"]
        W["Project workspace<br/>model.py"]
        E["CadFlow<br/>geometry kernel"]
        G["Geometry + Scene<br/>verification"]
        W --> E --> G
    end

    subgraph L4["4 · Artifacts and learning signals"]
        A["Canonical Scene Artifact"]
        T["Run events, source<br/>and geometric metrics"]
        D["Dataset + evaluation records<br/>(project direction)"]
        T -. "curate at scale" .-> D
    end

    U --> UI
    API --> C
    H --> W
    G -->|valid| A
    G -. "measured repair evidence" .-> H
    A --> UI
    C -. "progress + previews" .-> UI
    H --> T
    G --> T
~~~

### 🔄 One modeling run

1. A user creates a Project and submits a complete CAD task.
2. The selected agent harness reads only the relevant Skills and writes the stable <code>model.py</code> entry point.
3. CadFlow executes the program; geometric checks return measured evidence when the result is invalid or incorrect.
4. A passing result becomes <code>model.scene.zip</code> for the Viewer, while the run's observable records and artifacts remain available for analysis and future data curation.

<a id="quick-start"></a>

## 🚀 Quick start

### Requirements

- Linux x86_64 with glibc 2.31 or newer and Python 3.12, or macOS 26 arm64
  with Python 3.13
- `curl` or `wget` for installing missing tools
- An OpenAI-compatible model endpoint and API key

The repository vendors one CadFlow wheel for each supported platform. `uv`
selects the matching wheel from the shared `pyproject.toml` and `uv.lock`:

- Linux: `cadflow-0.1.0-cp312-cp312-linux_x86_64.whl` (SHA256
  `753c513fee879258a561efa9d3edf7e73ebe904ed160264caf5851c20b99854f`)
- macOS: `cadflow-0.1.0-cp313-cp313-macosx_26_0_arm64.whl` (SHA256
  `738bcccab01a8152831a871f3103790feb4d975c1e98357b887fc4ebe56391fa`)

### Install

~~~bash
./setup.sh
~~~

The setup script detects the platform, installs `uv` and a compatible Node.js
version when needed, syncs the locked Python and Viewer dependencies, and
creates `.env` from the example without overwriting an existing file. Use
`./setup.sh --check` to validate an existing environment without changing it.

Configure the model provider in <code>.env</code>:

~~~dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=<model-id>
OPENAI_API_KEY=<api-key>
~~~

### Launch

~~~bash
./run.sh
~~~

`run.sh` detects the operating system and architecture and selects the matching
Python interpreter automatically.

Open [http://localhost:5678](http://localhost:5678) to create a Project, choose
the available agent harness, submit a modeling task, follow live progress, and
inspect the final 3D result. The backend API is available at
<code>http://localhost:8765</code>. `TEXT_TO_CAD_HOST` and
`TEXT_TO_CAD_FRONTEND_HOST` may be set when another machine needs to reach the
trusted local demo.

### Runtime configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| <code>OPENAI_BASE_URL</code> | — | Base URL for the OpenAI-compatible provider. |
| <code>OPENAI_MODEL_ID</code> | — | Model identifier used by the agent harnesses. |
| <code>OPENAI_API_KEY</code> | — | Provider credential. Never commit it. |
| <code>TEXT_TO_CAD_HOST</code> | <code>0.0.0.0</code> | Backend bind host. |
| <code>TEXT_TO_CAD_PORT</code> | <code>8765</code> | Backend port. |
| <code>TEXT_TO_CAD_FRONTEND_HOST</code> | <code>0.0.0.0</code> | Viewer bind host. |
| <code>TEXT_TO_CAD_FRONTEND_PORT</code> | <code>5678</code> | Viewer port. |
| <code>TEXT_TO_CAD_PROJECTS_ROOT</code> | <code>output/projects</code> | Persistent Project workspace root. |
| <code>CADFLOW_PREVIEW_TIMEOUT_SECONDS</code> | <code>15</code> | Live preview worker wall-clock budget; restart the backend after changing it. |

## 📐 Current modeling contract

The current application still has a deliberately narrower output contract:
each Project starts with a non-passing `code/model.py` scaffold and keeps this
stable entry point:

~~~python
import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    # The agent replaces this scaffold with the requested part.
    raise NotImplementedError
~~~

The Agent's writable workspace is the Project's `code/` directory. It may read
and write only `*.py` files there, including local helper modules. Project
metadata, conversation logs, previews, review evidence, and CAD artifacts stay
outside that workspace. Skills are available through a separate read-only
reference mount; repository examples are not mounted into the Agent workspace.
The returned value must still be one valid `cad.Shape` with one solid and
positive volume. The backend creates `artifacts/model.scene.zip` after
validating the returned Shape and keeps STEP conversion internal to the Scene
bridge. These runtime paths and alternative inputs or output types do not
change the Project contract.

<a id="cad-skill-layer"></a>

## 🧠 CAD Skill layer

Skills provide task-specific modeling knowledge and exact API references without loading an entire CAD manual into every agent run.

| Skill | Focus |
| --- | --- |
| [<code>cadflow-model-part</code>](skills/cadflow-model-part/SKILL.md) | Parametric rigid parts, sketches, features, booleans, blends, and single-part delivery. |
| [<code>cadflow-flexible-model</code>](skills/cadflow-flexible-model/SKILL.md) | Static cloth, leather, membranes, garments, and other flexible geometry. |
| [<code>cadflow-step-brep</code>](skills/cadflow-step-brep/SKILL.md) | STEP/BREP inspection, feature inference, reconstruction, and evidence-based comparison. |
| [<code>cadflow-model-assembly</code>](skills/cadflow-model-assembly/SKILL.md) | Multi-part product and assembly structure, placement, and acceptance. |
| [<code>cadflow-rotary-transmission</code>](skills/cadflow-rotary-transmission/SKILL.md) | Rotary joints, gears, shafts, and transmission mechanisms. |

## 🗂️ Repository map

~~~text
CadFlowAgent/
├── backend/          Agent runtime, Project API, execution, events, and validation
├── viewer/           Browser workspace and Three.js Scene viewer
├── skills/           Progressive CadFlow workflows and API references
├── examples/         Parts, flexible models, assemblies, and reconstruction data
├── tests/            Runtime, boundary, repair-loop, and integration tests
└── vendor/           Platform-specific CadFlow wheel
~~~

Explore the [example catalog](examples/README.md), including mechanical parts, complex assemblies, flexible geometry, and the reconstruction trajectory seed.

## ✅ Checks

~~~bash
# Linux
uv run --python 3.12 pytest

# macOS
uv run --python 3.13 pytest

cd viewer && npm run build
~~~

Live provider tests are opt-in with `-m live_agent`.

## 📄 License

CadFlowAgent is licensed under the [GNU Affero General Public License v3.0](LICENSE).
