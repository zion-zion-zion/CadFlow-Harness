# Artifacts and results

Project runtime files are stored below the configured
`TEXT_TO_CAD_PROJECTS_ROOT` (default `output/projects`). A typical Project has
this layout:

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

`model.scene.zip` is the canonical Scene Artifact consumed by the Three.js
Viewer. The backend validates its schema, member hashes, and render assets
before exposing it. `model.step` is an internal bridge and downloadable
product file; it is not the Agent's source of truth.

## Product bundle

`product.json` describes a `part` or `assembly` result and its content-addressed
files. The bundle can include:

- `model.semantic.json` for Assembly structure and Part definitions;
- `parts/<part-id>.step` files for unique manufactured Parts;
- `bom.json` for quantities and component paths;
- `assumptions.json` for declared non-critical assumptions;
- `validation.json` for deterministic checks and evidence;
- `source.zip` for the Python source snapshot.

After acceptance, the bundle is copied into a versioned `artifacts/vNNNN/`
directory and `current.json` points to the active version. The default
retention limit is ten accepted versions per Project and can be changed with
`CADFLOW_ARTIFACT_VERSION_LIMIT`.

## Traces and downloads

Conversation and progress records stay as JSONL inside the Project. The API
offers bounded trace inspection and a redacted NDJSON download; credentials are
redacted before records are returned. Large tool results are kept separately so
the conversation context remains bounded.
