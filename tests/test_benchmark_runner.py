from __future__ import annotations

import json
from pathlib import Path

from benchmark.run_benchmark import aggregate_results, parse_cases, seed_project_code


def test_benchmark_prompt_catalog_contains_twenty_stable_cases() -> None:
    cases = parse_cases()

    assert len(cases) == 20
    assert [case.case_id for case in cases] == [
        *(f"single_part-{index:02d}" for index in range(1, 11)),
        *(f"assembly-{index:02d}" for index in range(1, 11)),
    ]
    assert all(case.prompt.strip() for case in cases)


def test_case_result_files_are_json_objects(tmp_path: Path) -> None:
    result = {
        "case_id": "single_part-01",
        "success": True,
    }
    path = tmp_path / "result-single_part-01.json"
    path.write_text(json.dumps(result), encoding="utf-8")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["case_id"] == "single_part-01"
    assert payload["success"] is True


def test_benchmark_success_rate_uses_strictly_more_than_eighty_percent(
    tmp_path: Path,
) -> None:
    for index, success in enumerate((True, True, True, True, False), start=1):
        (tmp_path / f"result-single_part-{index:02d}.json").write_text(
            json.dumps({"success": success}), encoding="utf-8"
        )

    results, rate = aggregate_results(tmp_path)

    assert len(results) == 5
    assert rate == 0.8
    assert not rate > 0.8


def test_seed_project_code_copies_only_python_source(tmp_path: Path) -> None:
    seed = tmp_path / "prior"
    (seed / "code" / "parts").mkdir(parents=True)
    (seed / "code" / "model.py").write_text("build_model = None\n", encoding="utf-8")
    (seed / "code" / "parts" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    (seed / "diagnostics.json").write_text("{}", encoding="utf-8")

    destination = tmp_path / "fresh" / "code"
    copied = seed_project_code(seed, destination)

    assert copied == (Path("model.py"), Path("parts/base.py"))
    assert (destination / "model.py").read_text(
        encoding="utf-8"
    ) == "build_model = None\n"
    assert (destination / "parts" / "base.py").exists()
    assert not (destination / "diagnostics.json").exists()
