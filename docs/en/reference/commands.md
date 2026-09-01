# Commands

Run commands from the repository root unless noted.

## Application

```bash
./setup.sh
./setup.sh --check
./run.sh
```

`setup.sh` installs or checks platform dependencies. `run.sh` starts the
backend and Viewer together and cleans up both child processes on exit.

## Documentation

Install the locked development group, including Zensical `0.0.57`, with:

```bash
uv sync --locked --group dev --python 3.12  # Linux
uv sync --locked --group dev --python 3.13  # macOS
```

Build both languages in strict mode and recreate `site/`:

```bash
./scripts/docs.sh build
```

The output is a deployable static tree with `/index.html`, `/en/`, and `/zh/`.
The explicit clean command is:

```bash
./scripts/docs.sh clean
```

Build first, then serve the combined site locally:

```bash
./scripts/docs.sh serve
```

The preview listens on `http://127.0.0.1:8000` by default. Set `DOCS_HOST` or
`DOCS_PORT` to change it. `zensical serve` can also preview one language, but
the repository script is the supported combined-site workflow.

## Verification

```bash
# Linux
uv run --locked --python 3.12 pytest
# macOS
uv run --locked --python 3.13 pytest
cd viewer && npm ci && npm run build
```

Live model-provider tests are opt-in with `-m live_agent`.
