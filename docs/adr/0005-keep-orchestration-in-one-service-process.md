# Keep orchestration in one service process

The local demo runs one Uvicorn worker and keeps active Deep Agent task handles, cancellation control, and a global one-run lock in the FastAPI process. Durable Project metadata, prompts, events, logs, source, and artifacts live under `output/projects/` with no database or external queue; after a restart the service rebuilds the Project Catalog from disk and marks any interrupted Running Project as Failed.
