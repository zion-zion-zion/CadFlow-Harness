# Repository Guidelines

## Project Structure & Module Organization

CadFlowAgent is a text-to-CAD agent workspace built around CadFlow. Python dependencies and project metadata live in `pyproject.toml`; exact versions are locked in `uv.lock`. The project currently targets Python 3.12/Linux x86_64 because the bundled CadFlow wheel is platform-specific. Runnable CAD examples belong in `examples/`. The browser-based scene viewer is isolated in `viewer/`, with TypeScript source under `viewer/src/` and Vite configuration beside it. Agent reference material is packaged under `skills/cadflow-model-part/`. `backend/` is currently a scaffold; place future server modules there and mirror them with tests under `tests/`. Generated CAD files and viewer build output are ignored and should not be committed.

## Build, Test, and Development Commands

- `uv sync --group dev` installs the Python environment and development tools.
- `uv run python examples/10_part_assembly.py` runs a representative CAD example.
- `uv run pytest` runs the Python test suite once tests exist under `tests/`.
- `cd viewer && npm ci` installs the locked frontend dependencies.
- `cd viewer && npm run dev` starts the Vite development server.
- `cd viewer && npm run build` type-checks TypeScript and creates a production build.

Run commands from the repository root unless the command explicitly enters `viewer/`.

## Coding Style & Naming Conventions

Use four-space indentation and standard PEP 8 naming for Python: `snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Add type hints to public backend interfaces. Keep CAD examples numbered and descriptively named, following `10_part_assembly.py`. In TypeScript, follow the existing two-space indentation, use `camelCase` for values, and `PascalCase` for components and types. Keep modules focused; split reusable logic from entry points.

## Testing Guidelines

Use `pytest`. Name files `tests/test_<module>.py` and tests `test_<behavior>()`. Every behavior change should include a focused regression test, including failure paths. For Viewer changes, `npm run build` is the minimum check; include manual verification notes when visual behavior changes.

## Commit & Pull Request Guidelines

Recent commits generally use Conventional Commit prefixes such as `feat:`, `fix:`, and `chore:`. Write imperative, scoped summaries, for example `feat: add model request endpoint`. Keep commits focused. Pull requests should explain the problem and solution, link relevant issues, list verification commands, and include screenshots for Viewer changes. Call out dependency or configuration changes explicitly.

## Security & Configuration

Copy `.env.example` to `.env` for local API configuration. Never commit `.env`, API keys, generated model artifacts, or credentials. Keep placeholders safe and update `.env.example` whenever required variables change.

## Agent skills

### Issue tracker

Issues and specs are tracked as local Markdown under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository with a root glossary and system-wide ADRs. See `docs/agents/domain.md`.
