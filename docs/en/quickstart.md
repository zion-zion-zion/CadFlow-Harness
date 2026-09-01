# Quick start

This page starts the local application from a clean checkout.

## Requirements

- Linux x86_64 with glibc 2.31 or newer and Python 3.12, or macOS 26 arm64 with Python 3.13.
- `curl` or `wget` if `setup.sh` needs to install `uv` or Node.js.
- Node.js 20.19+, 22.12+, or a newer major version, plus `npm`.
- An OpenAI-compatible model endpoint, model identifier, and API key.

The matching CadFlow wheel is in `vendor/` and selected by the platform markers in `pyproject.toml`. Other operating systems and Python versions are outside the bundled wheel's support range.

## Install

From the repository root:

```bash
./setup.sh
```

The script detects the platform, installs missing tools when possible, runs `uv sync --locked --group dev`, installs `viewer/` with `npm ci`, and creates `.env` from `.env.example` without replacing an existing file. Run `./setup.sh --check` to inspect an existing setup without changing it.

Set the model provider in `.env`:

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=<model-id>
OPENAI_API_KEY=<api-key>
```

Leave `OPENAI_BASE_URL` blank to use the provider default. See [Configuration](reference/configuration.md) for reasoning and review settings.

## Start the app

```bash
./run.sh
```

The script starts FastAPI on `http://localhost:8765` and the Vite Viewer on `http://localhost:5678`. Open the Viewer URL in a browser. When `viewer/dist` exists, the backend serves it from the same origin; during local development Vite proxies `/api` to the backend.

Set `TEXT_TO_CAD_HOST`, `TEXT_TO_CAD_PORT`, `TEXT_TO_CAD_FRONTEND_HOST`, or `TEXT_TO_CAD_FRONTEND_PORT` before running the script to change the bind addresses or ports. Keep the application off untrusted networks.

## First run

1. Create a named Project in the Project Catalog.
2. Enter a complete part or product request and submit it.
3. Watch the conversation and Run Progress panels while the Agent edits and validates the source.
4. Inspect the live preview if one is available. After the Run succeeds, inspect the accepted Scene and product tabs.

For the API flow, see [Create your first Project](first-project.md). For a build check that does not call a model, see [Commands](reference/commands.md).

## Common startup problems

| Symptom | Check |
| --- | --- |
| `uv` or Node.js is missing | Run `./setup.sh`, or install the tools and then run `./setup.sh --check`. |
| Unsupported platform or Python | Use Linux x86_64 + Python 3.12 or macOS 26 arm64 + Python 3.13. |
| Port already in use | Change the corresponding `TEXT_TO_CAD_*_PORT` variable and retry. |
| Run fails before the model starts | Confirm `OPENAI_API_KEY` and `OPENAI_MODEL_ID` in `.env`; inspect the Viewer error and backend log. |
| Preview is unavailable | The preview may fail or arrive late. Wait for validation or use the accepted Scene, then inspect the preview diagnostics panel. |
