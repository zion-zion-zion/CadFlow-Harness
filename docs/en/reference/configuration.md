# Configuration

Copy `.env.example` to `.env` with `./setup.sh`, then fill the provider
credentials. Exported shell variables take precedence over values loaded from
`.env`.

## Model and Agent settings

| Variable | Required | Effective value or default | Purpose |
| --- | --- | --- | --- |
| `OPENAI_BASE_URL` | No | Provider default when blank | OpenAI-compatible API base URL. |
| `OPENAI_MODEL_ID` | Yes | None | Model identifier for the Agent harness. |
| `OPENAI_API_KEY` | Yes | None | Provider credential; never commit it. |
| `OPENAI_REVIEW_MODEL_ID` | No | Uses `OPENAI_MODEL_ID` when blank | Optional model for `cad_review`. |
| `OPENAI_REASONING_EFFORT` | No | `.env.example`: `medium`; when unset, no explicit effort is sent | `none`, `low`, `medium`, `high`, or `max`. `none` explicitly selects Chat Completions. |
| `OPENAI_REASONING_SUMMARY` | No | Blank | Responses summary: `auto`, `concise`, or `detailed`. |
| `CADFLOW_AGENT_RUN_TIMEOUT_SECONDS` | No | `1200` | Per-Run wall-clock budget. |

## Persistence and preview

| Variable | Default | Purpose |
| --- | --- | --- |
| `CADFLOW_CONVERSATION_MAX_CONTEXT_CHARS` | `200000` | Maximum conversation context passed to the Agent. |
| `CADFLOW_ARTIFACT_VERSION_LIMIT` | `10` | Accepted artifact versions retained per Project. |
| `CADFLOW_PREVIEW_TIMEOUT_SECONDS` | Code default `15`; `.env.example` sets `60` | Live-preview worker wall-clock budget. |
| `TEXT_TO_CAD_PROJECTS_ROOT` | `output/projects` | Persistent Project catalog and artifact root. |

## Network settings

`run.sh` uses these defaults:

| Variable | Default in `run.sh` | Purpose |
| --- | --- | --- |
| `TEXT_TO_CAD_HOST` | `0.0.0.0` | FastAPI bind address. |
| `TEXT_TO_CAD_PORT` | `8765` | FastAPI port when started by `run.sh`. |
| `TEXT_TO_CAD_FRONTEND_HOST` | `0.0.0.0` | Vite bind address. |
| `TEXT_TO_CAD_FRONTEND_PORT` | `5678` | Vite development port. |

Running `uv run --python <version> python -m backend` directly uses the
entrypoint defaults `127.0.0.1:8000`; use `run.sh` for the paired 8765/5678
workflow.

!!! danger

    The application executes generated Python locally. Binding it beyond a
    trusted machine or network is outside the current security model.
