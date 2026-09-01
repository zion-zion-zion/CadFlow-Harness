# Create your first Project

The web Viewer is the normal entry point, but the same workflow is exposed by
the FastAPI API.

## Browser workflow

1. Open `http://localhost:5678` after [starting the app](quickstart.md).
2. In **Project Catalog**, enter a name and create the Project. New Projects
   start in `Draft` with an empty `code/model.py` scaffold.
3. In the current Project's message box, describe the complete geometry you
   want. Include units, important dimensions, holes or interfaces, and whether
   the result is a single part or a multi-part product.
4. Select **Send**. The Project moves to `Running`; the Agent harness writes
   Python source, invokes validation, and may revise the source when evidence
   identifies a failure.
5. Follow the conversation, progress events, and preview status. A successful
   run exposes the accepted Scene, product summary, validation report, and
   downloads in the Viewer.

## API workflow

Create a Project:

```bash
curl -sS -X POST http://localhost:8765/api/projects \
  -H 'content-type: application/json' \
  -d '{"name":"first-bracket"}'
```

Submit a task using the returned `project_id`:

```bash
curl -sS -X POST http://localhost:8765/api/projects/<project_id>/messages \
  -H 'content-type: application/json' \
  -d '{"message":"Build a 40 mm x 30 mm x 8 mm mounting plate with two 5 mm through holes.","request_id":"first-request"}'
```

The response includes the persisted turn, Project status, and whether an
accepted Scene is available. Poll `GET /api/projects/<project_id>` or open the
Viewer to follow the final state.

## Make the request precise

The Agent can infer non-critical dimensions, but it must preserve user-critical
requirements. State the coordinate-independent facts that matter, such as
overall envelope, material or appearance assumptions, mounting interfaces, and
whether separate manufactured parts should remain an Assembly.
