# Skills and run records

## CAD Skills

Skills are task-specific Markdown references mounted read-only during a Run.
The public catalog is:

| Skill | Use it for |
| --- | --- |
| [`cadflow-model-part`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-part) | Dimensioned rigid parts, sketches, features, booleans, blends, and single-part delivery. |
| [`cadflow-flexible-model`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-flexible-model) | Static cloth, leather, membranes, garments, and other flexible geometry. |
| [`cadflow-step-brep`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-step-brep) | STEP/BREP inspection, reconstruction, section analysis, and comparison by measurements. |
| [`cadflow-model-assembly`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-assembly) | Multi-part products, placements, connectors, constraints, and acceptance. |
| [`cadflow-rotary-transmission`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-rotary-transmission) | Gears, shafts, bearings, housings, and rotary mechanisms. |
| [`cadflow-scene-presentation`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-scene-presentation) | Requested colors, material appearance, edge styling, and cameras. |

Skills are implementation references. They do not change the executor contract;
the Agent still has to return a valid `Shape` or semantic `Assembly` accepted by
the current runtime.

## Run records

Each turn can write conversation JSONL, progress events, source revisions,
execution diagnostics, geometric measurements, token counts, and product files.
Provider credentials and large payloads are limited or redacted. The API exposes
live SSE and trace routes, plus a redacted NDJSON download.

These records help locate failures and review repairs without storing hidden
chain-of-thought. The repository currently contains one small reconstruction-data
example and no production data pipeline.
