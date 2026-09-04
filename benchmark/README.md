# CadFlow Harness CADTestBench benchmark

This directory contains the reproducible `smoke-5` benchmark. It measures the
production CadFlow Agent on five fixed, detailed, single-part samples from the
MIT-licensed [CADTestBench](https://huggingface.co/datasets/dimitrismallis/CADTestBench)
dataset. The suite manifest records the upstream revision, original prompt,
reviewed product-only prompt, source hash, test IDs, and one suite-wide scale
factor.

## Run

Prepare the repository `.env` with a real OpenAI-compatible endpoint, model ID,
and key, then run:

```bash
cd benchmark
./run.sh --suite smoke-5
```

Use `--sample 00000633` to debug one fixed sample or `--limit 2` to run a
prefix. `--offline` requires the cached CADTestBench parquet file and all
selected upstream reference source files to already be present. The default
evaluator is provisioned with `uv run --no-project --with cadquery --with
pyarrow` in a separate interpreter. Set
`CADFLOW_CADTESTBENCH_PYTHON` to an already prepared evaluator Python when
offline or when a dedicated environment is required.

The command creates a new, non-overwriting directory under `benchmark/runs/`
containing `config.json`, `suite-manifest.json`, `summary.json`,
`samples.csv`, `report.md`, and one `samples/<sample_id>/` record with the
prompt, Project metadata, diagnostics, event/conversation logs, source
snapshot, candidate STEP when available, and external evaluation result.

## Evaluation contract

The Agent sees only the fixed product prompt. It cannot access CADTestBench
parquet files, CADTests, reference source, reference geometry, or failure
assertions. The evaluator imports the final CadFlow STEP, scales it back to the
CADTestBench coordinate system, and runs the hidden CADTests in a separate
process. Before scoring, that process exports and re-imports each upstream
reference model through the same STEP path and records every bridge-compatible
test. Bridge-incompatible tests remain listed with their requirement/category
and exclusion reason; they are never silently filtered.

The five samples use a single scale factor of `1000.0` so the very thin source
geometry is numerically comfortable for OpenCascade. The evaluator applies the
inverse transform; topology and spatial relationships are unchanged.

`summary.json` reports generation validity, strict pass rate (all compatible
tests for a sample), requirement score (fraction of requirement groups whose
tests all pass), CADTest pass rate, category accuracy, completion/candidate and
timeout rates, Harness acceptance, false acceptance/rejection, first
validation, CAD execution count, wall time, token use, review failures, and
bridge compatibility. Values are `null` when no evidence exists, never an
invented zero.

## Reproduction and cost

The run config records the branch, commit, dataset revision, model metadata,
reasoning configuration, suite hash, and selected IDs. The first run downloads
only the detailed CADTests parquet into `benchmark/.cache/`; it is ignored by
Git and may be retained for offline reruns. Every sample calls the real model
configured in `.env`, so provider latency and usage charges apply. Credentials
are removed from diagnostics and reports.

## Limitations

This is a fixed five-sample smoke suite, not `dev-50` or `full-200`. The source
dataset stores small normalized unit dimensions; scaling is fixed for the whole
suite rather than tuned per sample. CADTestBench's evaluator uses CadQuery in a
separate environment, so its environment must be provisioned once before a
fully offline run.
