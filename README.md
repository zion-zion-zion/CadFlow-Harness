# CadFlowAgent

CadFlowAgent is a trusted-local runtime for agents that write executable
parametric CAD programs, validate geometry with CadFlow, and preserve the
observable run as reusable project evidence. It is not a one-shot mesh or image
generator: the generated Python remains readable and editable.

The current alpha supports a FastAPI Project/Run API, a Vite/Three.js Viewer,
the `deepagents` harness, `cad.Shape` single-part results, semantic
`cad.Assembly` products, deterministic validation, independent CAD review, live
previews, and versioned artifacts. Large-scale datasets and CAD-specific
post-training are future directions, not shipped model capabilities.

## Documentation

- [English documentation site](https://zion-zion-zion.github.io/CadFlowAgent/en/)
- [简体中文文档站](https://zion-zion-zion.github.io/CadFlowAgent/zh/)
- [English documentation source](docs/en/index.md)
- [中文文档源](docs/zh/index.md)

## Quick start

```bash
./setup.sh
# Configure OPENAI_MODEL_ID and OPENAI_API_KEY in .env
./run.sh
```

Open `http://localhost:5678` for the Viewer and `http://localhost:8765/docs`
for the API reference. See the [quick start guide](docs/en/quickstart.md) for
supported platforms, configuration, and first Project steps.

## Checks

```bash
# Linux
uv run --locked --python 3.12 pytest
# macOS
uv run --locked --python 3.13 pytest
cd viewer && npm run build
cd .. && ./scripts/docs.sh build
```

CadFlowAgent is licensed under the [GNU Affero General Public License v3.0](LICENSE).
