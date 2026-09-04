"""Standalone CADTestBench evaluator process.

This module is deliberately invoked in a separate interpreter by the benchmark
runner. The Agent never receives this module, the downloaded parquet tables, or
the upstream reference source.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import math
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--scale-factor", type=float, required=True)
    parser.add_argument("--cadtests-parquet", type=Path, required=True)
    parser.add_argument("--reference-url", required=True)
    parser.add_argument("--cadtest-ids", required=True, help="JSON array of manifest-approved CADTest IDs")
    parser.add_argument("--bridge-only", action="store_true")
    args = parser.parse_args()
    try:
        cadtest_ids = {int(value) for value in json.loads(args.cadtest_ids)}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid --cadtest-ids: {exc}") from exc
    rows = _load_rows(args.cadtests_parquet, args.sample_id, cadtest_ids)
    found_ids = {int(row["cadtest_id"]) for row in rows}
    if found_ids != cadtest_ids:
        missing = ", ".join(str(value) for value in sorted(cadtest_ids - found_ids))
        raise SystemExit(f"CADTest parquet is missing manifest test ID(s): {missing}")
    result = evaluate(
        candidate=args.candidate,
        rows=rows,
        scale_factor=args.scale_factor,
        reference_url=args.reference_url,
        bridge_only=args.bridge_only,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def evaluate(
    *,
    candidate: Path,
    rows: list[dict[str, Any]],
    scale_factor: float,
    reference_url: str,
    bridge_only: bool = False,
) -> dict[str, Any]:
    if scale_factor <= 0 or not math.isfinite(scale_factor):
        raise ValueError("scale_factor must be finite and positive")
    bridge = _bridge_check(rows, reference_url)
    compatible_ids = {
        int(row["cadtest_id"])
        for row in rows
        if int(row["cadtest_id"]) in bridge["compatible_test_ids"]
    }
    if bridge_only:
        return {"bridge": bridge}
    rows = [row for row in rows if int(row["cadtest_id"]) in compatible_ids]
    try:
        model = _load_candidate(candidate, scale_factor)
    except Exception as exc:
        entries = [_entry(row, "invalid", f"{type(exc).__name__}: {exc}") for row in rows]
        return {
            "status": "invalid",
            "error": f"{type(exc).__name__}: {exc}",
            "cadtests": entries,
            "bridge": bridge,
            "counts": {"total": len(entries), "passed": 0, "failed": len(entries)},
            "requirement_groups": [],
            "category_breakdown": {},
            "strict_pass": False,
            "requirement_score": None,
        }

    import cadquery as cq

    namespace = {"final_result": model, "cq": cq}
    entries = [_run_test(row, namespace) for row in rows]
    passed = {int(entry["id"]) for entry in entries if entry["status"] == "pass"}
    groups = _groups(rows, passed)
    categories = _categories(entries)
    total = len(entries)
    passed_count = len(passed)
    return {
        "status": "valid",
        "cadtests": entries,
        "bridge": bridge,
        "counts": {"total": total, "passed": passed_count, "failed": total - passed_count},
        "requirement_groups": groups,
        "category_breakdown": categories,
        "strict_pass": bool(total and passed_count == total),
        "requirement_score": (
            sum(bool(group["all_passed"]) for group in groups) / len(groups)
            if groups
            else None
        ),
    }


def _load_rows(path: Path, sample_id: str, cadtest_ids: set[int]) -> list[dict[str, Any]]:
    import pyarrow.parquet as parquet

    rows = parquet.read_table(path).to_pylist()
    return [row for row in rows if row.get("sample_id") == sample_id and int(row["cadtest_id"]) in cadtest_ids]


def _load_candidate(path: Path, scale_factor: float) -> Any:
    import cadquery as cq

    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"candidate STEP is missing: {path}")
    imported = cq.importers.importStep(str(path))
    values = imported.vals()
    if len(values) != 1:
        raise ValueError(f"candidate STEP contains {len(values)} top-level values")
    shape = values[0].scale(1.0 / scale_factor)
    if hasattr(shape, "isValid") and not shape.isValid():
        raise ValueError("candidate STEP contains invalid geometry")
    if hasattr(shape, "Solids") and len(shape.Solids()) != 1:
        raise ValueError("candidate STEP does not contain one solid after scaling")
    if hasattr(shape, "Volume") and shape.Volume() <= 0:
        raise ValueError("candidate STEP has non-positive volume")
    return cq.Workplane("XY").newObject([shape])


def _bridge_check(rows: list[dict[str, Any]], reference_url: str) -> dict[str, Any]:
    try:
        source = urllib.request.urlopen(reference_url, timeout=30).read().decode("utf-8")
        with tempfile.TemporaryDirectory(prefix="cadflow-bridge-") as temp:
            reference_step = Path(temp) / "reference.step"
            reference = _execute_reference(source)
            import cadquery as cq

            cq.exporters.export(reference, str(reference_step))
            replayed = cq.importers.importStep(str(reference_step))
            values = replayed.vals()
            if len(values) != 1:
                raise ValueError("reference STEP has multiple top-level values")
            model = cq.Workplane("XY").newObject([values[0]])
            namespace = {"final_result": model, "cq": cq}
            compatible: list[int] = []
            reasons: dict[str, str] = {}
            for row in rows:
                entry = _run_test(row, namespace)
                test_id = int(row["cadtest_id"])
                if entry["status"] == "pass":
                    compatible.append(test_id)
                else:
                    reasons[str(test_id)] = str(entry.get("message") or "test failed")
            return {
                "status": "checked",
                "upstream_test_count": len(rows),
                "compatible_test_count": len(compatible),
                "compatibility_rate": len(compatible) / len(rows) if rows else None,
                "compatible_test_ids": compatible,
                "bridge_incompatible": {
                    str(row["cadtest_id"]): {
                        "category": row.get("cadtest_type"),
                        "requirement_id": row.get("requirement_id"),
                        "reason": reasons.get(str(row["cadtest_id"]), "unknown"),
                    }
                    for row in rows
                    if int(row["cadtest_id"]) not in compatible
                },
            }
    except Exception as exc:
        return {
            "status": "bridge_error",
            "upstream_test_count": len(rows),
            "compatible_test_count": 0,
            "compatibility_rate": 0.0 if rows else None,
            "compatible_test_ids": [],
            "error": f"{type(exc).__name__}: {exc}",
            "bridge_incompatible": {
                str(row["cadtest_id"]): {
                    "category": row.get("cadtest_type"),
                    "requirement_id": row.get("requirement_id"),
                    "reason": f"bridge setup failed: {type(exc).__name__}: {exc}",
                }
                for row in rows
            },
        }


def _execute_reference(source: str) -> Any:
    import cadquery as cq

    ast.parse(source)
    sanitized = "\n".join(
        line for line in source.splitlines() if "cq.exporters.export" not in line
    )
    namespace: dict[str, Any] = {"cq": cq, "__name__": "__main__"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(sanitized, "reference.py", "exec"), namespace)
    for name in ("final_result", "part", "result"):
        if name in namespace:
            return namespace[name]
    raise ValueError("reference source did not define a model value")


def _run_test(row: Mapping[str, Any], namespace: dict[str, Any]) -> dict[str, Any]:
    code = str(row.get("cadtest_code") or "")
    namespace["_check_pass_msg"] = None
    preamble = (
        "import math\n"
        "def check(condition, pass_msg, fail_msg):\n"
        "    global _check_pass_msg\n"
        "    if not condition:\n"
        "        raise AssertionError(fail_msg)\n"
        "    _check_pass_msg = pass_msg\n"
    )
    try:
        exec(preamble + code, namespace)
        return _entry(row, "pass", namespace.get("_check_pass_msg"))
    except AssertionError as exc:
        return _entry(row, "fail", str(exc) or "cadtest failed")
    except Exception as exc:
        return _entry(row, "fail", f"{type(exc).__name__}: {exc}", type(exc).__name__)


def _entry(row: Mapping[str, Any], status: str, message: str | None, exception: str | None = None) -> dict[str, Any]:
    result = {
        "id": int(row["cadtest_id"]),
        "category": row.get("cadtest_type") or "uncategorized",
        "requirement_id": row.get("requirement_id"),
        "requirement_type": row.get("requirement_type"),
        "requirement_description": row.get("requirement_description"),
        "description": row.get("cadtest_description"),
        "status": status,
        "message": message,
    }
    if exception:
        result["exception"] = exception
    return result


def _groups(rows: list[dict[str, Any]], passed: set[int]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("requirement_id")
        if rid is None:
            continue
        key = str(rid)
        bucket = grouped.setdefault(
            key,
            {
                "requirement_id": rid,
                "requirement_type": row.get("requirement_type"),
                "requirement_description": row.get("requirement_description"),
                "cadtest_ids": [],
            },
        )
        bucket["cadtest_ids"].append(int(row["cadtest_id"]))
    result = []
    for bucket in grouped.values():
        ids = bucket["cadtest_ids"]
        n_passed = sum(item in passed for item in ids)
        result.append({**bucket, "total": len(ids), "passed": n_passed, "all_passed": n_passed == len(ids)})
    return result


def _categories(entries: list[dict[str, Any]]) -> dict[str, dict[str, int | float]]:
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0, "failed": 0})
    for entry in entries:
        bucket = buckets[str(entry["category"])]
        bucket["total"] += 1
        if entry["status"] == "pass":
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    return {
        name: {**counts, "pass_rate": counts["passed"] / counts["total"] if counts["total"] else None}
        for name, counts in buckets.items()
    }


if __name__ == "__main__":
    raise SystemExit(main())
