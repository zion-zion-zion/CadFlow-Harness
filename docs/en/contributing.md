# Contributing

Contributions should keep the runtime contract, public API, and generated
artifacts honest. Start by reading the repository [AGENTS.md](https://github.com/zion-zion-zion/CadFlowAgent/blob/master/AGENTS.md)
for engineering conventions; its internal issue and triage notes are not part
of this public site.

## Development environment

```bash
./setup.sh
uv sync --locked --group dev --python 3.12  # Linux
uv sync --locked --group dev --python 3.13  # macOS
cd viewer && npm ci
```

Use the platform-specific Python selected by `setup.sh`. Do not commit `.env`,
generated `output/`, `examples/out/`, `viewer/dist/`, or credentials.

## Checks before a pull request

```bash
uv run --locked --python 3.12 pytest
cd viewer && npm run build
cd .. && ./scripts/docs.sh build
```

Add focused regression tests for backend behavior changes. Viewer changes need
at least a production build and a manual note when visual behavior changes.
Documentation changes must build both languages in strict mode.

## Documentation changes

Keep corresponding pages in `docs/en/` and `docs/zh/` aligned in scope and
facts. Link to source files or API routes that exist today, label experimental
examples, and update the matching navigation entry. The public `docs/` tree is
reserved for users and external contributors; internal Agent operating rules
live under `.agents/`.

## Pull requests

Explain the user problem, the change, and verification commands. Include a
short screenshot note for Viewer changes and call out dependency or
configuration changes. The documentation workflow builds strict English and
Chinese sites on pull requests and publishes Pages after `master` updates.
