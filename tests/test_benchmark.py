from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.dataset import FORBIDDEN_PROMPT_TERMS, load_suite, normalize_prompt, validate_product_prompt
from benchmark.runner import _check_bridge_manifest, aggregate, run_samples, write_reports


def test_smoke_suite_manifest_is_fixed_and_auditable() -> None:
    suite = load_suite()
    assert suite.suite_id == "smoke-5"
    assert suite.dataset["name"] == "dimitrismallis/CADTestBench"
    assert suite.dataset["revision"]
    assert suite.scale_factor == 1000.0
    assert [sample.sample_id for sample in suite.samples] == [
        "00000633", "00001490", "00004154", "00006578", "00520150"
    ]
    assert all(sample.input_sha256 for sample in suite.samples)
    bridge = json.loads((Path(__file__).parents[1] / "benchmark/suites/smoke-5-bridge.json").read_text())
    assert all(bridge["samples"][sample.sample_id]["compatibility_rate"] == 1.0 for sample in suite.samples)


def test_product_prompts_contain_no_implementation_terms() -> None:
    suite = load_suite()
    for sample in suite.samples:
        validate_product_prompt(sample.product_prompt)
        lowered = sample.product_prompt.casefold()
        assert not [term for term in FORBIDDEN_PROMPT_TERMS if term in lowered]


def test_prompt_normalization_is_deterministic_and_scales_units() -> None:
    source = "Write Python code using CADQuery to create a block 0.75 units long."
    first = normalize_prompt(source, scale_factor=1000.0)
    assert first == normalize_prompt(source, scale_factor=1000.0)
    assert "750" in first
    assert "Python" not in first and "CADQuery" not in first


def test_invalid_product_prompt_is_rejected() -> None:
    with pytest.raises(ValueError, match="implementation terms"):
        validate_product_prompt("Use Python and CADQuery to create a part")


def test_aggregate_reports_null_when_data_is_missing(tmp_path: Path) -> None:
    suite = load_suite()
    summary = aggregate(
        [
            {
                "sample_id": suite.samples[0].sample_id,
                "project_state": "Failed",
                "harness_accepts": False,
                "candidate_available": False,
                "strict_pass": False,
                "requirement_score": None,
                "evaluation": {"counts": {"total": 0, "passed": 0, "failed": 0}, "category_breakdown": {}},
                "diagnostics": {},
            }
        ],
        suite,
    )
    assert summary["task_count"] == 1
    assert summary["cadtest_pass_rate"] is None
    assert summary["requirement_score"] is None
    assert summary["mean_cad_executions"] is None
    assert summary["invalid_rate"] == 0.0
    assert summary["unassessed_rate"] == 1.0


def test_aggregate_calculates_strict_rs_and_harness_mismatch() -> None:
    suite = load_suite()
    rows = [
        {
            "sample_id": suite.samples[0].sample_id,
            "project_state": "Succeeded",
            "harness_accepts": True,
            "candidate_available": True,
            "strict_pass": False,
            "requirement_score": 0.5,
            "evaluation": {"counts": {"total": 2, "passed": 1, "failed": 1}, "category_breakdown": {"topology_checks": {"total": 2, "passed": 1, "failed": 1}}},
            "diagnostics": {"cad_execution_count": 2, "execution_results": [{"product_validation_status": "Passed"}], "review_result": {"status": "fail"}},
        },
        {
            "sample_id": suite.samples[1].sample_id,
            "project_state": "Failed",
            "harness_accepts": False,
            "candidate_available": True,
            "strict_pass": True,
            "requirement_score": 1.0,
            "evaluation": {"counts": {"total": 2, "passed": 2, "failed": 0}, "category_breakdown": {"topology_checks": {"total": 2, "passed": 2, "failed": 0}}},
            "diagnostics": {"cad_execution_count": 1, "execution_results": [{"product_validation_status": "Draft"}]},
        },
    ]
    summary = aggregate(rows, suite)
    assert summary["strict_pass_rate"] == 0.5
    assert summary["requirement_score"] == 0.75
    assert summary["false_acceptance_rate"] == 0.5
    assert summary["false_rejection_rate"] == 0.5
    assert summary["category_accuracy"]["topology_checks"]["pass_rate"] == 0.75


def test_report_generation_writes_machine_and_human_outputs(tmp_path: Path) -> None:
    suite = load_suite()
    summary = aggregate([], suite)
    write_reports(tmp_path, suite, summary, [])
    assert json.loads((tmp_path / "summary.json").read_text())['suite_id'] == "smoke-5"
    assert (tmp_path / "samples.csv").is_file()
    assert "Known limitations" in (tmp_path / "report.md").read_text()


def test_report_helpers_do_not_echo_api_keys() -> None:
    from backend.cad_security import redact_credentials

    assert "secret-value" not in redact_credentials("OPENAI_API_KEY=secret-value")


def test_dynamic_bridge_must_match_versioned_manifest() -> None:
    suite = load_suite()
    sample = suite.samples[0]
    evaluation = {
        "status": "valid",
        "strict_pass": True,
        "bridge": {
            "status": "checked",
            "upstream_test_count": len(sample.cadtest_ids),
            "compatible_test_ids": list(sample.cadtest_ids),
        },
    }
    assert _check_bridge_manifest(suite, sample, evaluation)["status"] == "valid"
    mismatched = {**evaluation, "bridge": {**evaluation["bridge"], "compatible_test_ids": []}}
    assert _check_bridge_manifest(suite, sample, mismatched)["status"] == "bridge_mismatch"


def test_runner_loop_can_record_a_failed_sample_and_continue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from benchmark import runner

    suite = load_suite()
    calls: list[str] = []

    def fake_run_sample(root: Path, current_suite: object, sample: object, current_settings: object) -> dict[str, object]:
        calls.append(sample.sample_id)
        if len(calls) == 1:
            raise RuntimeError("sample failed")
        return {"sample_id": sample.sample_id}

    monkeypatch.setattr(runner, "run_sample", fake_run_sample)
    rows = run_samples(tmp_path, suite, suite.samples[:2], object())
    assert calls == [suite.samples[0].sample_id, suite.samples[1].sample_id]
    assert rows[0]["evaluation"]["status"] == "runner_error"
    assert rows[1] == {"sample_id": suite.samples[1].sample_id}
