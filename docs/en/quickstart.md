# Quick start

This guide starts the trusted local demo from a clean checkout.

## Requirements

- Linux x86_64 with glibc 2.31 or newer and Python 3.12, or macOS 26 arm64
  with Python 3.13.
- `curl` or `wget` if `setup.sh` needs to install `uv` or Node.js.
- Node.js 20.19+, 22.12+, or a newer major version, plus `npm`.
- An OpenAI-compatible model endpoint, model identifier, and API key.

The matching CadFlow wheel is vendored in `vendor/` and selected by the
platform markers in `pyproject.toml`. Other operating systems and Python
versions are not supported by the bundled wheel.

## Install

From the repository root:

```bash
./setup.sh
```

The script detects the platform, installs missing tools when possible, runs
`uv sync --locked --group dev`, installs `viewer/` with `npm ci`, and creates
`.env` from `.env.example` without overwriting an existing file. To check an
existing machine without changes, run `./setup.sh --check`.

Configure the required model settings in `.env`:

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=<model-id>
OPENAI_API_KEY=<api-key>
```

`OPENAI_BASE_URL` may be blank for the default provider. Reasoning and review
options are documented in [Configuration](reference/configuration.md).

## Start the app

```bash
./run.sh
```

The script starts the FastAPI backend on `http://localhost:8765` and the Vite
Viewer on `http://localhost:5678`. Open the Viewer URL in a browser. The
backend is same-origin for a production `viewer/dist` build; during local
development Vite proxies `/api` to the backend.

To bind the services to different interfaces or ports, set
`TEXT_TO_CAD_HOST`, `TEXT_TO_CAD_PORT`, `TEXT_TO_CAD_FRONTEND_HOST`, or
`TEXT_TO_CAD_FRONTEND_PORT` before running the script. Keep the application on
a trusted network.

## First-run checklist

1. Create a named Project in the Project Catalog.
2. Enter a complete part or product request and submit it.
3. Watch the conversation and Run Progress panels while the Agent edits and
   validates the source.
4. Inspect the live preview when available, then inspect the accepted Scene and
   product tabs after the Run succeeds.

For an API-driven walkthrough, see [First Project](first-project.md). For a
browser-free build check, see [Commands](reference/commands.md).

## Common startup problems

| Symptom | Check |
| --- | --- |
| `uv` or Node.js is missing | Run `./setup.sh` or install the tools, then `./setup.sh --check`. |
| Unsupported platform or Python | Use Linux x86_64 + Python 3.12 or macOS 26 arm64 + Python 3.13. |
| Port already in use | Change the corresponding `TEXT_TO_CAD_*_PORT` variable and retry. |
| Run fails before the model starts | Confirm `OPENAI_API_KEY` and `OPENAI_MODEL_ID` are set in `.env`; inspect the Viewer error and backend log. |
| Preview is unavailable | A preview is best effort. Wait for validation or use the accepted Scene; inspect the preview diagnostics panel for the reported error. |
