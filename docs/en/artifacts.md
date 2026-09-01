# Artifacts and results

Project files live below `TEXT_TO_CAD_PROJECTS_ROOT` (default
`output/projects`). A typical Project has this layout:

```text
<project-id>/
├── project.json
├── prompt.txt
├── code/
│   ├── model.py
│   └── helpers.py
├── conversation.jsonl
├── events.jsonl
├── diagnostics.json
├── previews/live/
│   ├── model.glb
│   └── status.json
├── artifacts/
│   ├── model.scene.zip
│   ├── model.step
│   ├── product.json
│   ├── model.semantic.json
│   ├── bom.json
│   ├── assumptions.json
│   ├── validation.json
│   └── source.zip
└── artifacts/v0001/...
```

## Viewer result

`model.scene.zip` is the Scene Artifact consumed by the Three.js Viewer. Before
serving it, the backend checks its schema, member hashes, and render assets.
`model.step` is an internal bridge and a downloadable product file; the Agent
source remains the source of truth.

## Product bundle

`product.json` describes a `part` or `assembly` result and its content-addressed
files. The bundle can include:

- `model.semantic.json` for Assembly structure and Part definitions;
- `parts/<part-id>.step` for each unique manufactured Part;
- `bom.json` for quantities and component paths;
- `assumptions.json` for declared non-critical assumptions;
- `validation.json` for checks and measurements;
- `source.zip` for the Python source snapshot.

After acceptance, the bundle is copied into `artifacts/vNNNN/`, and
`current.json` points to the active version. Each Project keeps ten accepted
versions by default; change this with `CADFLOW_ARTIFACT_VERSION_LIMIT`.

## Traces and downloads

Conversation and progress records are JSONL files inside the Project. The API
offers limited trace inspection and a redacted NDJSON download; credentials are
removed before records are returned. Large tool results are stored separately so
they do not fill the conversation context.
