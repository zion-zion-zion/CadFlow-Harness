# API

While `run.sh` is running, FastAPI serves interactive OpenAPI documentation at
`http://localhost:8765/docs`. The machine-readable schema is at `/openapi.json`
and ReDoc is at `/redoc`.

## Projects and messages

| Method | Route | Use |
| --- | --- | --- |
| `GET` | `/api/projects` | List Projects. |
| `POST` | `/api/projects` | Create a Project with `{ "name": "..." }`. |
| `GET` | `/api/projects/{project_id}` | Read Project status and artifact summary. |
| `DELETE` | `/api/projects/{project_id}` | Delete after name confirmation. |
| `POST` | `/api/projects/{project_id}/messages` | Start a conversation turn. Body includes `message`, `request_id`, optional `retry_of`, and optional `harness`. |
| `GET` | `/api/projects/{project_id}/messages` | Read conversation turns. |
| `DELETE` | `/api/projects/{project_id}/conversation` | Clear conversation after confirmation. |
| `POST` | `/api/projects/{project_id}/stop` | Stop the active Run. |

The older one-shot route `/api/projects/{project_id}/run` remains for API
compatibility. It accepts `prompt` and an optional `harness`.

## Previews and artifacts

| Method | Route | Use |
| --- | --- | --- |
| `GET` | `/api/projects/{project_id}/preview/status` | Read live-preview state. |
| `GET` | `/api/projects/{project_id}/preview` | Download the latest usable GLB preview. |
| `POST` | `/api/projects/{project_id}/preview/retry` | Request another preview build. |
| `POST` | `/api/projects/{project_id}/preview/pause` | Pause or resume preview work. |
| `GET` | `/api/projects/{project_id}/scene` | Download the accepted `model.scene.zip`. |
| `GET` | `/api/projects/{project_id}/product` | Read the accepted product manifest projection. |
| `GET` | `/api/projects/{project_id}/product/manifest` | Download `product.json`. |
| `GET` | `/api/projects/{project_id}/product/files/{role}` | Download a product file by role. |
| `GET` | `/api/projects/{project_id}/product/part-step?part_id=...` | Download one unique Part STEP. |

## Run records

| Method | Route | Use |
| --- | --- | --- |
| `GET` | `/api/traces` | List limited trace summaries. |
| `GET` | `/api/projects/{project_id}/events` | Stream progress as Server-Sent Events. |
| `GET` | `/api/projects/{project_id}/trace` | Read a paginated, filterable trace. |
| `GET` | `/api/projects/{project_id}/trace/download` | Download redacted NDJSON. |
| `GET` | `/api/projects/{project_id}/trace/events?cursor=...` | Read one trace event. |
