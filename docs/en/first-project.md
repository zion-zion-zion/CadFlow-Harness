# Create your first Project

The Web Viewer is the usual entry point. The same workflow is available through
the FastAPI API.

## Browser workflow

1. [Start the app](quickstart.md), then open `http://localhost:5678`.
2. In **Project Catalog**, enter a name and create the Project. A new Project
   starts in `Draft` with an initial `code/model.py` scaffold.
3. In the Project message box, describe the geometry. Include units, dimensions,
   holes or interfaces, and whether the result is one part or a multi-part
   product.
4. Select **Send**. The Project moves to `Running`; the Agent Harness writes
   Python, runs validation, and edits the source when checks find a problem.
5. Follow the conversation, progress events, and preview status. A successful
   Run exposes the accepted Scene, product summary, validation report, and
   download links in the Viewer.

## API workflow

Create a Project:

```bash
curl -sS -X POST http://localhost:8765/api/projects \
  -H 'content-type: application/json' \
  -d '{"name":"first-bracket"}'
```

Submit a task with the returned `project_id`:

```bash
curl -sS -X POST http://localhost:8765/api/projects/<project_id>/messages \
  -H 'content-type: application/json' \
  -d '{"message":"Build a 40 mm x 30 mm x 8 mm mounting plate with two 5 mm through holes.","request_id":"first-request"}'
```

The response contains the saved turn, Project status, and whether an accepted
Scene is available. Poll `GET /api/projects/<project_id>` or open the Viewer to
follow the final state.

## Write a precise request

The Agent can infer non-critical dimensions, but it cannot choose your critical
requirements. State the overall envelope, material or appearance assumptions,
mounting interfaces, and whether separate manufactured parts must remain an
Assembly.
