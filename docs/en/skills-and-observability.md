# Skills and observability

## CAD Skills

Skills are progressive, task-specific Markdown references mounted read-only for
an Agent Run. The public catalog is:

| Skill | Use it for |
| --- | --- |
| [`cadflow-model-part`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-part) | Dimensioned rigid parts, sketches, features, booleans, blends, and single-Part delivery. |
| [`cadflow-flexible-model`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-flexible-model) | Static cloth, leather, membranes, garments, and other flexible geometry. |
| [`cadflow-step-brep`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-step-brep) | STEP/BREP inspection, reconstruction, section analysis, and evidence-based comparison. |
| [`cadflow-model-assembly`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-model-assembly) | Multi-part products, placements, connectors, constraints, and acceptance. |
| [`cadflow-rotary-transmission`](https://github.com/zion-zion-zion/CadFlowAgent/tree/master/skills/cadflow-rotary-transmission) | Gears, shafts, bearings, housings, and rotary mechanisms. |

Skills guide implementation; they do not widen the executor contract. The
Agent still must return a valid `Shape` or semantic `Assembly` accepted by the
current runtime.

## Observable records

Each turn can produce conversation JSONL, progress events, source revisions,
execution diagnostics, geometric measurements, token counts, and product
artifacts. Provider credentials and large payloads are bounded or redacted.
The API exposes live SSE events and trace endpoints for inspection and a
redacted NDJSON download.

These records make failures and repairs measurable without storing hidden
chain-of-thought. They can support evaluation or dataset curation later, but a
small reconstruction-data example is the only packaged data workflow today.
