# Repository Guidelines

## Project Structure & Module Organization

CadFlow Harness is a text-to-CAD agent workspace built around CadFlow. Python dependencies and project metadata live in `pyproject.toml`; exact versions are locked in `uv.lock`. The bundled CadFlow wheels support Python 3.12/Linux x86_64 with glibc 2.31+ and Python 3.13/macOS 12 arm64; `run.sh` selects the matching interpreter. Runtime Agent skills live under `skills/`, with one `SKILL.md`-based workflow per child directory. Runnable CAD examples belong in `examples/`; single-file examples use `cadflow_<subject>.py`, while larger product and assembly examples use descriptive module directories. The FastAPI application lives in `backend/` and is mirrored by tests under `tests/`. The browser-based scene viewer is isolated in `viewer/`, with TypeScript source under `viewer/src/` and Vite configuration beside it. Generated CAD files and viewer build output are ignored and should not be committed.

## Build, Test, and Development Commands

- `uv sync --group dev --python 3.12` on Linux or `uv sync --group dev --python 3.13` on macOS installs the Python environment and development tools.
- `uv run --python <platform-version> python examples/cadflow_complex_mounting_bracket.py` runs a representative single-part CAD example.
- `uv run --python <platform-version> pytest` runs the Python test suite.
- `cd viewer && npm ci` installs the locked frontend dependencies.
- `cd viewer && npm run dev` starts the Vite development server.
- `cd viewer && npm run build` type-checks TypeScript and creates a production build.

Run commands from the repository root unless the command explicitly enters `viewer/`.

## Coding Style & Naming Conventions

Use four-space indentation and standard PEP 8 naming for Python: `snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Add type hints to public backend interfaces. Name focused examples `cadflow_<subject>.py`; use a descriptive directory with a `main.py` entry point when an assembly needs multiple modules. In TypeScript, follow the existing two-space indentation, use `camelCase` for values, and `PascalCase` for components and types. Keep modules focused; split reusable logic from entry points.

## Testing Guidelines

Use `pytest`. Name files `tests/test_<module>.py` and tests `test_<behavior>()`. Every behavior change should include a focused regression test, including failure paths. For Viewer changes, `npm run build` is the minimum check; include manual verification notes when visual behavior changes.

## Commit & Pull Request Guidelines

Recent commits generally use Conventional Commit prefixes such as `feat:`, `fix:`, and `chore:`. Write imperative, scoped summaries, for example `feat: add model request endpoint`. Keep commits focused. Pull requests should explain the problem and solution, link relevant issues, list verification commands, and include screenshots for Viewer changes. Call out dependency or configuration changes explicitly.

## Security & Configuration

Copy `.env.example` to `.env` for local API configuration. Never commit `.env`, API keys, generated model artifacts, or credentials. Keep placeholders safe and update `.env.example` whenever required variables change.

## Agent skills

### Issue tracker

Issues and specs are tracked in GitHub Issues via the `gh` CLI. See `.agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage label vocabulary. See `.agents/triage-labels.md`.

### Domain docs

This is a single-context repository with a root glossary and system-wide ADRs. See `.agents/domain.md`.
