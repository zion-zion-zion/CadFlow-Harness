"""Run the production CadFlow Agent and evaluate its final STEP output."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

BENCHMARK_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = BENCHMARK_ROOT.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.agent import AgentRunService, AgentSettings  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.cad_security import redact_credentials  # noqa: E402
from backend.projects import ProjectState  # noqa: E402
from .dataset import BenchmarkSuite, SuiteSample, load_suite  # noqa: E402


DEFAULT_CACHE = BENCHMARK_ROOT / ".cache"
DATASET_TESTS = DEFAULT_CACHE / "cadtests-detailed.parquet"
REFERENCE_CACHE = DEFAULT_CACHE / "references"


@dataclass
class SnapshotService:
    """Delegate to the production service while preserving failed candidates."""

    delegate: AgentRunService
    destination: Path

    def run(self, project_id: str, prompt: str, **kwargs: Any) -> Any:
        outcome = self.delegate.run(project_id, prompt, **kwargs)
        project_dir = self.delegate.store.project_directory(project_id)
        self.destination.mkdir(parents=True, exist_ok=True)
        code_dir = project_dir / "code"
        if code_dir.is_dir():
            shutil.copytree(code_dir, self.destination / "source", dirs_exist_ok=True)
        artifacts = project_dir / "artifacts"
        if artifacts.is_dir():
            shutil.copytree(artifacts, self.destination / "candidate-artifacts", dirs_exist_ok=True)
        return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="smoke-5")
    parser.add_argument("--sample", action="append", dest="samples")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if args.suite != "smoke-5":
        parser.error("only the versioned smoke-5 suite is available")
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    suite = load_suite()
    selected = select_samples(suite, args.samples, args.limit)
    settings = AgentSettings.from_environment()
    run_root = args.output or _new_run_root()
    run_root.mkdir(parents=True, exist_ok=False)
    _write_run_config(run_root, suite, settings, selected)
    ensure_dataset_cache(suite, DATASET_TESTS, offline=args.offline)
    ensure_reference_cache(suite, selected, offline=args.offline)
    rows = run_samples(run_root, suite, selected, settings)
    summary = aggregate(rows, suite)
    write_reports(run_root, suite, summary, rows)
    print(f"SUMMARY run={run_root} strict_pass_rate={summary['strict_pass_rate']}", flush=True)
    return 0


def run_samples(
    run_root: Path,
    suite: BenchmarkSuite,
    samples: tuple[SuiteSample, ...],
    settings: AgentSettings,
) -> list[dict[str, Any]]:
    """Run every selected sample, isolating a runner exception to one row."""

    rows: list[dict[str, Any]] = []
    for index, sample in enumerate(samples, 1):
        print(f"START {index}/{len(samples)} {sample.sample_id}", flush=True)
        try:
            row = run_sample(run_root, suite, sample, settings)
        except Exception as exc:
            row = {
                "sample_id": sample.sample_id,
                "project_id": None,
                "project_state": None,
                "harness_accepts": False,
                "failure_reason": redact_credentials(str(exc)),
                "wall_time_seconds": None,
                "candidate_available": False,
                "evaluation": {
                    "status": "runner_error",
                    "strict_pass": False,
                    "requirement_score": None,
                    "cadtests": [],
                    "category_breakdown": {},
                    "counts": {"total": 0, "passed": 0, "failed": 0},
                    "bridge": {"status": "not_run"},
                    "error": redact_credentials(f"{type(exc).__name__}: {exc}"),
                },
                "strict_pass": False,
                "requirement_score": None,
                "bridge": {"status": "not_run"},
                "diagnostics": {},
            }
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ("sample_id", "project_state", "strict_pass", "failure_reason")}, ensure_ascii=False), flush=True)
    return rows


def select_samples(suite: BenchmarkSuite, ids: list[str] | None, limit: int | None) -> tuple[SuiteSample, ...]:
    if ids:
        unknown = set(ids) - {item.sample_id for item in suite.samples}
        if unknown:
            raise SystemExit(f"unknown sample id(s): {', '.join(sorted(unknown))}")
        selected = tuple(item for item in suite.samples if item.sample_id in ids)
    else:
        selected = suite.samples
    if limit is not None:
        if limit < 1:
            raise SystemExit("--limit must be positive")
        selected = selected[:limit]
    return selected


def run_sample(run_root: Path, suite: BenchmarkSuite, sample: SuiteSample, settings: AgentSettings) -> dict[str, Any]:
    sample_root = run_root / "samples" / sample.sample_id
    sample_root.mkdir(parents=True, exist_ok=True)
    sample_input = sample.to_dict()
    sample_input.update(
        {
            "dataset": suite.dataset,
            "normalization": suite.normalization,
            "suite_id": suite.suite_id,
            "suite_manifest_sha256": suite.manifest_sha256,
        }
    )
    (sample_root / "input.json").write_text(json.dumps(sample_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_root = sample_root / "project"
    started = time.monotonic()
    project_id: str | None = None
    state: str | None = None
    failure_reason: str | None = None
    candidate: Path | None = None
    snapshot = sample_root / "generation"
    try:
        app = create_app(projects_root=project_root)
        base_service = app.state.run_coordinator.run_service
        app.state.run_coordinator.run_service = SnapshotService(base_service, snapshot)
        with _test_client(app) as client:
            created = client.post("/api/projects", json={"name": f"CADTestBench {sample.sample_id}"})
            created.raise_for_status()
            project_id = str(created.json()["project_id"])
            started_response = client.post(f"/api/projects/{project_id}/run", json={"prompt": sample.product_prompt})
            started_response.raise_for_status()
            state_payload = _wait_for_terminal(client, project_id, settings.run_timeout_seconds + 90)
            state = str(state_payload.get("state"))
            store = app.state.project_store
            diagnostics = store.read_diagnostics(project_id) or {}
            failure_reason = state_payload.get("failure_reason")
            project_dir = store.project_directory(project_id)
            _copy_file(project_dir / "diagnostics.json", sample_root / "diagnostics.json")
            _copy_file(project_dir / "events.jsonl", sample_root / "events.jsonl")
            _copy_file(project_dir / "conversation.jsonl", sample_root / "conversation.jsonl")
            _copy_file(project_dir / "project.json", sample_root / "project.json")
            _copy_file(project_dir / "code" / "model.py", sample_root / "source" / "model.py")
            candidate = _find_candidate(store, project_id, snapshot)
        if candidate is not None:
            retained_candidate = sample_root / "candidate.step"
            _copy_file(candidate, retained_candidate)
            candidate = retained_candidate
        evaluation = evaluate_candidate(suite, sample, candidate)
    except Exception as exc:
        evaluation = {"status": "runner_error", "strict_pass": False, "requirement_score": None, "cadtests": [], "category_breakdown": {}, "counts": {"total": 0, "passed": 0, "failed": 0}, "bridge": {"status": "not_run"}, "error": redact_credentials(f"{type(exc).__name__}: {exc}")}
        failure_reason = redact_credentials(str(exc))
    row = {
        "sample_id": sample.sample_id,
        "project_id": project_id,
        "project_state": state,
        "harness_accepts": state == ProjectState.SUCCEEDED.value,
        "failure_reason": redact_credentials(str(failure_reason)) if failure_reason else None,
        "wall_time_seconds": round(time.monotonic() - started, 3),
        "candidate_available": candidate is not None,
        "evaluation": evaluation,
        "strict_pass": bool(evaluation.get("strict_pass")),
        "requirement_score": evaluation.get("requirement_score"),
        "bridge": evaluation.get("bridge"),
        "diagnostics": diagnostics if "diagnostics" in locals() else {},
    }
    (sample_root / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def evaluate_candidate(suite: BenchmarkSuite, sample: SuiteSample, candidate: Path | None) -> dict[str, Any]:
    if candidate is None:
        return {"status": "missing_candidate", "strict_pass": False, "requirement_score": None, "cadtests": [], "category_breakdown": {}, "counts": {"total": 0, "passed": 0, "failed": 0}, "bridge": {"status": "not_run"}}
    reference_path = REFERENCE_CACHE / sample.sample_id / "Python_Code.py"
    reference_url = reference_path.as_uri() if reference_path.is_file() else f"https://raw.githubusercontent.com/Kamel773/CAD_Code_Generation/{suite.dataset['upstream_reference_revision']}/{sample.reference_path}"
    command = [
        *_evaluator_command(), "-m", "benchmark.evaluator_worker", "--candidate", str(candidate), "--sample-id", sample.sample_id,
        "--scale-factor", str(suite.scale_factor), "--cadtests-parquet", str(DATASET_TESTS), "--reference-url", reference_url,
        "--cadtest-ids", json.dumps(list(sample.cadtest_ids)),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPOSITORY_ROOT) + os.pathsep + str(BENCHMARK_ROOT / ".evaluator-site")
    try:
        completed = subprocess.run(command, cwd=REPOSITORY_ROOT, env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        return {"status": "evaluator_timeout", "error": f"evaluator exceeded 300 seconds: {exc}", "strict_pass": False, "requirement_score": None, "cadtests": [], "category_breakdown": {}, "counts": {"total": 0, "passed": 0, "failed": 0}, "bridge": {"status": "not_reported"}}
    if completed.returncode != 0:
        return {"status": "evaluator_error", "error": redact_credentials(completed.stderr[-3000:]), "strict_pass": False, "requirement_score": None, "cadtests": [], "category_breakdown": {}, "counts": {"total": 0, "passed": 0, "failed": 0}, "bridge": {"status": "not_reported"}}
    try:
        evaluation = json.loads(completed.stdout.strip().splitlines()[-1])
        return _check_bridge_manifest(suite, sample, evaluation)
    except (json.JSONDecodeError, IndexError):
        return {"status": "evaluator_protocol_error", "error": "evaluator did not return JSON", "strict_pass": False, "requirement_score": None, "cadtests": [], "category_breakdown": {}, "counts": {"total": 0, "passed": 0, "failed": 0}, "bridge": {"status": "not_reported"}}


def _check_bridge_manifest(suite: BenchmarkSuite, sample: SuiteSample, evaluation: dict[str, Any]) -> dict[str, Any]:
    bridge = evaluation.get("bridge")
    if not isinstance(bridge, Mapping) or bridge.get("status") != "checked":
        return evaluation
    manifest_path = suite.manifest_path.with_name("smoke-5-bridge.json")
    if not manifest_path.is_file():
        return evaluation
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))["samples"][sample.sample_id]
        actual_ids = sorted(int(value) for value in bridge.get("compatible_test_ids", []))
        expected_ids = sorted(int(value) for value in expected["compatible_test_ids"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {**evaluation, "status": "bridge_manifest_error", "strict_pass": False, "error": "bridge manifest is malformed"}
    if actual_ids != expected_ids or bridge.get("upstream_test_count") != expected.get("upstream_test_count"):
        return {
            **evaluation,
            "status": "bridge_mismatch",
            "strict_pass": False,
            "error": f"dynamic bridge result does not match {manifest_path.name}",
        }
    return evaluation


def aggregate(rows: list[dict[str, Any]], suite: BenchmarkSuite) -> dict[str, Any]:
    total = len(rows)
    completed = sum(row.get("project_state") in {"Succeeded", "Failed", "Stopped"} for row in rows)
    candidates = sum(bool(row.get("candidate_available")) for row in rows)
    valid = sum(row.get("evaluation", {}).get("status") == "valid" for row in rows)
    invalid = sum(row.get("evaluation", {}).get("status") == "invalid" for row in rows)
    strict = sum(bool(row.get("strict_pass")) for row in rows)
    requirement_scores = [row["requirement_score"] for row in rows if isinstance(row.get("requirement_score"), (int, float))]
    tests_total = sum(row.get("evaluation", {}).get("counts", {}).get("total", 0) for row in rows)
    tests_passed = sum(row.get("evaluation", {}).get("counts", {}).get("passed", 0) for row in rows)
    accepted = sum(bool(row.get("harness_accepts")) for row in rows)
    false_acceptance = sum(row.get("harness_accepts") and not row.get("strict_pass") for row in rows)
    false_rejection = sum((not row.get("harness_accepts")) and row.get("strict_pass") for row in rows)
    category: dict[str, dict[str, int]] = {}
    failed_requirements: dict[str, int] = {}
    bridge_counts = [row.get("bridge", {}).get("compatibility_rate") for row in rows if isinstance(row.get("bridge", {}).get("compatibility_rate"), (int, float))]
    for row in rows:
        for name, counts in row.get("evaluation", {}).get("category_breakdown", {}).items():
            bucket = category.setdefault(name, {"total": 0, "passed": 0, "failed": 0})
            for key in bucket:
                bucket[key] += int(counts.get(key, 0))
        for test in row.get("evaluation", {}).get("cadtests", []):
            if isinstance(test, Mapping) and test.get("status") != "pass":
                requirement = str(test.get("requirement_id") or "unassigned")
                failed_requirements[requirement] = failed_requirements.get(requirement, 0) + 1
    for counts in category.values():
        counts["pass_rate"] = counts["passed"] / counts["total"] if counts["total"] else None
    return {
        "schema_version": "cadflow-benchmark-summary/v1", "suite_id": suite.suite_id, "task_count": total,
        "run_completion_rate": completed / total if total else None, "candidate_rate": candidates / total if total else None,
        "valid_part_rate": valid / total if total else None, "invalid_rate": invalid / total if total else None,
        "unassessed_rate": (total - valid - invalid) / total if total else None,
        "timeout_rate": sum("timeout" in str(row.get("failure_reason", "")).casefold() or row.get("evaluation", {}).get("status") == "evaluator_timeout" for row in rows) / total if total else None,
        "strict_pass_rate": strict / total if total else None, "requirement_score": sum(requirement_scores) / len(requirement_scores) if requirement_scores else None,
        "cadtest_pass_rate": tests_passed / tests_total if tests_total else None, "category_accuracy": category,
        "failed_requirements": dict(sorted(failed_requirements.items(), key=lambda item: (-item[1], item[0]))),
        "harness_acceptance_rate": accepted / total if total else None, "false_acceptance_rate": false_acceptance / total if total else None,
        "false_rejection_rate": false_rejection / total if total else None, "first_validation_pass_rate": _first_validation_rate(rows),
        "mean_cad_executions": _mean_executions(rows), "mean_wall_time": _mean(rows, "wall_time_seconds"),
        "token_usage": _token_totals(rows), "mean_token_usage": _mean_token_usage(rows), "review_failure_rate": _review_failure_rate(rows),
        "bridge_compatibility_rate": sum(bridge_counts) / len(bridge_counts) if bridge_counts else None,
        "bridge_compatibility_reason": None if bridge_counts else "no evaluator bridge result was available",
        "truth_counts": {"true_success": sum(row.get("harness_accepts") and row.get("strict_pass") for row in rows), "false_acceptance": false_acceptance, "false_rejection": false_rejection, "true_failure": total - strict - false_acceptance - false_rejection},
    }


def write_reports(run_root: Path, suite: BenchmarkSuite, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (run_root / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "project_id", "project_state", "harness_accepts", "candidate_available", "strict_pass", "requirement_score", "failure_reason"])
        writer.writeheader(); writer.writerows({key: row.get(key) for key in writer.fieldnames} for row in rows)
    lines = [f"# {suite.suite_id} benchmark", "", "## Overall", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "", "## Samples", ""]
    for row in rows:
        evaluation = row.get("evaluation", {})
        failures = [f"{item.get('id')} ({item.get('requirement_id')}, {item.get('category')}): {item.get('message')}" for item in evaluation.get('cadtests', []) if isinstance(item, Mapping) and item.get('status') != 'pass']
        lines.append(f"- `{row['sample_id']}`: state={row.get('project_state')}, harness_accepts={row.get('harness_accepts')}, strict_pass={row.get('strict_pass')}, RS={row.get('requirement_score')}, candidate={row.get('candidate_available')}; reason={row.get('failure_reason') or evaluation.get('error') or 'none'}")
        if failures:
            lines.extend([f"  - failed test: {failure}" for failure in failures])
    lines.extend(["", "## Category accuracy", ""])
    for name, counts in summary.get("category_accuracy", {}).items():
        lines.append(f"- `{name}`: {counts['passed']}/{counts['total']} passed ({counts['pass_rate']:.3f})")
    lines.extend(["", "## Most frequent failed requirements", ""])
    for name, count in summary.get("failed_requirements", {}).items():
        lines.append(f"- `{name}`: {count} failed test(s)")
    lines.extend(["", "## Run cost and diagnostics", "", f"- Mean wall time: `{summary.get('mean_wall_time')}` seconds", f"- Mean CAD executions: `{summary.get('mean_cad_executions')}`", f"- Mean token usage: `{json.dumps(summary.get('mean_token_usage'), ensure_ascii=False)}`", f"- Bridge compatibility: `{summary.get('bridge_compatibility_rate')}`"])
    lines.extend(["", "## Known limitations", "", "- CADTestBench is evaluated in a separately provisioned CadQuery interpreter; the evaluator receives only the final STEP.", "- Metrics are `null` when no evidence exists. Missing candidates and evaluator errors are reported as unassessed rather than invalid geometry.", "- The suite is fixed to five detailed single-part samples and is not a replacement for the full benchmark."])
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_dataset_cache(suite: BenchmarkSuite, destination: Path, *, offline: bool) -> None:
    if destination.is_file() and destination.stat().st_size > 0:
        return
    if offline:
        raise RuntimeError(f"offline mode requested but dataset cache is missing: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    import urllib.request
    url = suite.dataset["cadtests_url"]
    with urllib.request.urlopen(url, timeout=120) as response:
        destination.write_bytes(response.read())


def ensure_reference_cache(suite: BenchmarkSuite, samples: tuple[SuiteSample, ...], *, offline: bool) -> None:
    import urllib.request
    for sample in samples:
        destination = REFERENCE_CACHE / sample.sample_id / "Python_Code.py"
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        if offline:
            raise RuntimeError(f"offline mode requested but reference cache is missing: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://raw.githubusercontent.com/Kamel773/CAD_Code_Generation/{suite.dataset['upstream_reference_revision']}/{sample.reference_path}"
        with urllib.request.urlopen(url, timeout=60) as response:
            destination.write_bytes(response.read())


def _wait_for_terminal(client: Any, project_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}"); response.raise_for_status(); payload = response.json()
        if payload.get("state") in {"Succeeded", "Failed", "Stopped"}:
            return payload
        time.sleep(1)
    raise TimeoutError(f"benchmark Project did not finish: {project_id}")


def _find_candidate(store: Any, project_id: str, snapshot: Path) -> Path | None:
    candidates = [snapshot / "candidate-artifacts" / "model.step"]
    try:
        candidates.insert(0, store.product_artifact(project_id).file_path("product_step"))
    except Exception:
        pass
    return next((path for path in candidates if path.is_file() and path.stat().st_size > 0), None)


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)


def _new_run_root() -> Path:
    return BENCHMARK_ROOT / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])


def _write_run_config(root: Path, suite: BenchmarkSuite, settings: AgentSettings, samples: tuple[SuiteSample, ...]) -> None:
    config = {"schema_version": "cadflow-benchmark-run/v1", "created_at": datetime.now(timezone.utc).isoformat(), "suite_id": suite.suite_id, "suite_manifest_sha256": suite.manifest_sha256, "git": _git_facts(), "model": {"provider": settings.provider, "base_url": _public_base_url(settings.base_url), "model_id": settings.model_id, "review_model_id": settings.review_model_id, "reasoning_effort": settings.reasoning_effort, "reasoning_summary": settings.reasoning_summary, "run_timeout_seconds": settings.run_timeout_seconds}, "samples": [sample.sample_id for sample in samples]}
    (root / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(suite.manifest_path, root / "suite-manifest.json")
    bridge = suite.manifest_path.with_name("smoke-5-bridge.json")
    if bridge.is_file():
        shutil.copy2(bridge, root / bridge.name)


def _git_facts() -> dict[str, str | None]:
    def git(*args: str) -> str | None:
        try: return subprocess.check_output(["git", *args], cwd=REPOSITORY_ROOT, text=True, stderr=subprocess.DEVNULL).strip() or None
        except Exception: return None
    return {"branch": git("branch", "--show-current"), "commit": git("rev-parse", "HEAD")}


def _public_base_url(value: str | None) -> str | None:
    """Keep endpoint identity while removing credential-like URL parameters."""

    if not value:
        return None
    parsed = urlsplit(value)
    hostname = parsed.hostname
    if hostname is not None:
        host = hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        parsed = parsed._replace(netloc=host)
    if not parsed.query:
        return redact_credentials(value)
    sensitive = ("api_key", "apikey", "token", "access_token", "secret", "password", "authorization")
    query = [
        (key, "[REDACTED]" if any(term in key.casefold() for term in sensitive) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _evaluator_command() -> list[str]:
    configured = os.environ.get("CADFLOW_CADTESTBENCH_PYTHON", "").strip()
    if configured:
        return configured.split()
    # CadQuery is intentionally provisioned for a separate evaluator process.
    # This keeps its OpenCascade dependency out of the production environment.
    return [
        "uv", "run", "--no-project", "--python", "3.13",
        "--with", "cadquery", "--with", "pyarrow", "python",
    ]


def _test_client(app: Any) -> Any:
    from fastapi.testclient import TestClient
    return TestClient(app)


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return sum(values) / len(values) if values else None


def _mean_executions(rows: list[dict[str, Any]]) -> float | None:
    values = [row.get("diagnostics", {}).get("cad_execution_count") for row in rows]
    values = [float(item) for item in values if isinstance(item, (int, float))]
    return sum(values) / len(values) if values else None


def _first_validation_rate(rows: list[dict[str, Any]]) -> float | None:
    values = [row.get("diagnostics", {}).get("execution_results") for row in rows]
    statuses = [items[0].get("product_validation_status") == "Passed" for items in values if isinstance(items, list) and items]
    return sum(statuses) / len(statuses) if statuses else None


def _token_totals(rows: list[dict[str, Any]]) -> dict[str, int] | None:
    usages = [row.get("diagnostics", {}).get("token_usage") for row in rows]
    usages = [item for item in usages if isinstance(item, Mapping)]
    if not usages: return None
    return {key: sum(int(item.get(key, 0)) for item in usages) for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")}


def _mean_token_usage(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    usages = [row.get("diagnostics", {}).get("token_usage") for row in rows]
    usages = [item for item in usages if isinstance(item, Mapping)]
    if not usages:
        return None
    return {key: sum(float(item.get(key, 0)) for item in usages) / len(usages) for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")}


def _review_failure_rate(rows: list[dict[str, Any]]) -> float | None:
    values = [row.get("diagnostics", {}).get("review_result") for row in rows]
    values = [item for item in values if isinstance(item, Mapping)]
    return sum(item.get("status") != "pass" for item in values) / len(values) if values else None


if __name__ == "__main__":
    raise SystemExit(main())
