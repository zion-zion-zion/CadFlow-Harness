"""Run the checked-in benchmark prompts through the production Project API.

This runner intentionally creates one isolated Project store per prompt.  It
uses the real configured Agent and reviewer; no model responses are supplied
by the runner.  Results are written to ``results.json`` under the requested
output directory so failed cases can be correlated with their conversation
trace and repair state.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

# Load the repository configuration before importing backend.app.  The runner
# never prints any environment values and does not modify the .env file.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

BENCHMARK_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = BENCHMARK_ROOT.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.agent import AgentSettings, resolve_agent_run_timeout_seconds  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.projects import ProjectState  # noqa: E402
from backend.scene_validation import validate_scene_artifact  # noqa: E402


PROMPT_FILES = (
    BENCHMARK_ROOT / "complex_single_part_prompts.md",
    BENCHMARK_ROOT / "complex_multi_part_assembly_prompts.md",
)


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    category: str
    name: str
    prompt: str
    source_file: str


@dataclass
class CaseResult:
    case_id: str
    category: str
    name: str
    project_id: str | None
    state: str | None
    success: bool
    elapsed_seconds: float
    validation_count: int
    review_count: int
    review_status: str | None
    scene_valid: bool
    scene_error: str | None
    failure_reason: str | None
    project_root: str | None


def aggregate_results(output_root: Path) -> tuple[list[dict[str, object]], float]:
    """Read independent case results and return rows plus the success rate."""

    files = sorted(output_root.glob("result-*.json"))
    if not files:
        files = sorted(output_root.glob("single_part-*/case_result.json")) + sorted(
            output_root.glob("assembly-*/case_result.json")
        )
    results = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    successes = sum(bool(item.get("success")) for item in results)
    return results, successes / len(results) if results else 0.0


def parse_cases() -> tuple[BenchmarkCase, ...]:
    cases: list[BenchmarkCase] = []
    for source_file in PROMPT_FILES:
        text = source_file.read_text(encoding="utf-8")
        category = "single_part" if "single_part" in source_file.name else "assembly"
        common_match = re.search(
            r"## 通用约束\s*\n\s*(.*?)(?=\n## \d+\.)",
            text,
            flags=re.DOTALL,
        )
        common = common_match.group(1).strip() if common_match else ""
        headings = list(re.finditer(r"^## (\d+)\. (.+)$", text, flags=re.MULTILINE))
        for index, heading in enumerate(headings):
            end = (
                headings[index + 1].start() if index + 1 < len(headings) else len(text)
            )
            section = text[heading.end() : end]
            prompt_match = re.search(
                r"### 提示词\s*\n\s*(.*?)\s*\n### 主要测试点",
                section,
                flags=re.DOTALL,
            )
            if prompt_match is None:
                raise ValueError(
                    f"missing prompt section in {source_file}:{heading.group(1)}"
                )
            prompt = "\n\n".join(
                part for part in (common, prompt_match.group(1).strip()) if part
            )
            case_id = f"{category}-{int(heading.group(1)):02d}"
            cases.append(
                BenchmarkCase(
                    case_id=case_id,
                    category=category,
                    name=heading.group(2).strip(),
                    prompt=prompt,
                    source_file=source_file.name,
                )
            )
    return tuple(cases)


def _wait_for_terminal(
    client: TestClient, project_id: str, timeout: float
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}")
        response.raise_for_status()
        project = response.json()
        if project["state"] in {
            ProjectState.SUCCEEDED.value,
            ProjectState.FAILED.value,
            ProjectState.STOPPED.value,
        }:
            return project
        time.sleep(1.0)
    raise TimeoutError(f"benchmark Project did not finish: {project_id}")


def seed_project_code(seed_root: Path, destination_code: Path) -> tuple[Path, ...]:
    """Copy only Python source from a prior Project into a fresh code root."""

    source_root = seed_root / "code" if (seed_root / "code").is_dir() else seed_root
    source_root = source_root.resolve()
    destination_code.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in sorted(source_root.rglob("*.py")):
        if not source.is_file():
            continue
        relative = source.relative_to(source_root)
        destination = destination_code / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative)
    return tuple(copied)


def run_case(
    case: BenchmarkCase,
    output_root: Path,
    timeout: float,
    seed_code: Path | None = None,
) -> CaseResult:
    case_root = output_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=True)
    app = create_app(projects_root=case_root)
    started = time.monotonic()
    project_id: str | None = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/projects", json={"name": case.name})
            created.raise_for_status()
            project_id = str(created.json()["project_id"])
            if seed_code is not None:
                store = app.state.project_store
                seed_project_code(
                    seed_code,
                    store.project_directory(project_id) / "code",
                )
            started_response = client.post(
                f"/api/projects/{project_id}/run",
                json={"prompt": case.prompt},
            )
            started_response.raise_for_status()
            project = _wait_for_terminal(client, project_id, timeout)
            store = app.state.project_store
            diagnostics = store.read_diagnostics(project_id) or {}
            ledger = diagnostics.get("attempt_ledger")
            attempts = ledger if isinstance(ledger, list) else []
            validation_count = sum(
                1
                for item in attempts
                if isinstance(item, dict) and item.get("attempt_kind") == "validation"
            )
            review_count = sum(
                1
                for item in attempts
                if isinstance(item, dict) and item.get("attempt_kind") == "review"
            )
            review = diagnostics.get("review_result")
            review_status = review.get("status") if isinstance(review, dict) else None
            scene_valid = False
            scene_error: str | None = None
            try:
                scene_path = store.scene_artifact(project_id)
                parsed = validate_scene_artifact(scene_path)
                scene_valid = parsed.valid
                scene_error = parsed.error
            except Exception as exc:
                scene_error = str(exc)
            execution = diagnostics.get("execution_result")
            validation_passed = (
                isinstance(execution, dict)
                and execution.get("product_validation_status") == "Passed"
            )
            success = (
                project.get("state") == ProjectState.SUCCEEDED.value
                and validation_passed
                and review_status == "pass"
                and scene_valid
            )
            result = CaseResult(
                case_id=case.case_id,
                category=case.category,
                name=case.name,
                project_id=project_id,
                state=str(project.get("state")),
                success=success,
                elapsed_seconds=round(time.monotonic() - started, 3),
                validation_count=validation_count,
                review_count=review_count,
                review_status=review_status,
                scene_valid=scene_valid,
                scene_error=scene_error,
                failure_reason=project.get("failure_reason"),
                project_root=str(store.project_directory(project_id)),
            )
            (case_root / "case_result.json").write_text(
                json.dumps(asdict(result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return result
    except Exception as exc:
        result = CaseResult(
            case_id=case.case_id,
            category=case.category,
            name=case.name,
            project_id=project_id,
            state=None,
            success=False,
            elapsed_seconds=round(time.monotonic() - started, 3),
            validation_count=0,
            review_count=0,
            review_status=None,
            scene_valid=False,
            scene_error=None,
            failure_reason=str(exc),
            project_root=str(case_root),
        )
        (case_root / "case_result.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=None, help="isolated output directory"
    )
    parser.add_argument(
        "--case", action="append", help="case id(s) to run, default: all"
    )
    parser.add_argument(
        "--timeout", type=float, default=None, help="per-case timeout seconds"
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="aggregate existing per-case result files",
    )
    parser.add_argument(
        "--seed-code",
        type=Path,
        default=None,
        help="copy Python source from a prior Project before the real run",
    )
    args = parser.parse_args()
    if args.aggregate:
        output_root = args.output or Path("/tmp/cadflow-benchmark")
        results, rate = aggregate_results(output_root)
        (output_root / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        successes = sum(bool(item.get("success")) for item in results)
        print(
            f"SUMMARY total={len(results)} success={successes} rate={rate:.3f} output={output_root}"
        )
        return 0 if results and rate > 0.8 else 1
    cases = parse_cases()
    selected = set(args.case or ())
    unknown = selected - {case.case_id for case in cases}
    if unknown:
        parser.error(f"unknown case id(s): {', '.join(sorted(unknown))}")
    if selected:
        cases = tuple(case for case in cases if case.case_id in selected)
    if args.seed_code is not None and len(cases) != 1:
        parser.error("--seed-code requires exactly one selected case")
    if args.seed_code is not None and not args.seed_code.is_dir():
        parser.error(f"seed code directory does not exist: {args.seed_code}")
    output_root = (
        args.output
        or Path("/tmp")
        / f"cadflow-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    timeout = args.timeout or resolve_agent_run_timeout_seconds() + 60.0
    settings = AgentSettings.from_environment()
    print(
        f"cases={len(cases)} model={settings.model_id} review_model={settings.review_model_id or settings.model_id}",
        flush=True,
    )
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        print(f"START {index}/{len(cases)} {case.case_id} {case.name}", flush=True)
        result = run_case(case, output_root, timeout, args.seed_code)
        results.append(result)
        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
        (output_root / f"result-{case.case_id}.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    successes = sum(item.success for item in results)
    print(
        f"SUMMARY total={len(results)} success={successes} rate={(successes / len(results) if results else 0):.3f} output={output_root}",
        flush=True,
    )
    return 0 if successes / len(results) > 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
