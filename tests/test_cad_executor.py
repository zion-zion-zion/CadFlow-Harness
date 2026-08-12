from __future__ import annotations

import math
import threading
import time
from pathlib import Path

from backend.cad_executor import (
    CADExecutor,
    CancellationToken,
    build_cad_environment,
)
from backend.model_source import create_model_source


def test_cancellation_token_preserves_the_first_cancellation_reason() -> None:
    caller = CancellationToken()
    caller.cancel()
    caller.cancel(reason="timeout")
    assert caller.cancellation_reason == "caller"

    timeout = CancellationToken()
    timeout.cancel(reason="timeout")
    timeout.cancel()
    assert timeout.cancellation_reason == "timeout"


def test_cad_execution_returns_validated_scene_facts(tmp_path: Path) -> None:
    create_model_source(tmp_path)

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.captured_solid_count == 1
    assert result.solid_volume is not None
    assert math.isfinite(result.solid_volume) and result.solid_volume > 0
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is True
    assert result.scene_parse_result.glb_asset_count == 2
    assert result.scene_parse_result.model_json_present is True
    assert result.artifact_entries == ("model.scene.zip",)


def test_nonzero_model_exit_is_observable(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "raise RuntimeError('model failed')\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=5.0)

    assert result.status == "failed"
    assert result.exit_code != 0
    assert "model failed" in (result.error or "")


def test_invalid_scene_artifact_is_not_promoted_to_success(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        scaffold.model_path.read_text(encoding="utf-8")
        + "\nSCENE_ARTIFACT.write_bytes(b'not a Scene ZIP')\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.captured_solid_count == 1
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is False
    assert "zip" in (result.error or "").lower()


def test_captured_solid_count_must_be_exactly_one(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = scaffold.model_path.read_text(encoding="utf-8")
    source = source.replace(
        "    scad.capture_result(value=final_solid)\n",
        "    scad.capture_result(value=final_solid)\n"
        "    second_solid = scad.make_box_rsolid(\n"
        "        width=2.0, height=2.0, depth=2.0,\n"
        "        bottom_face_center=(20.0, 0.0, 0.0),\n"
        "    )\n"
        "    scad.capture_result(value=second_solid)\n",
    )
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.captured_solid_count == 2
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is True
    assert "exactly one" in (result.error or "")


def test_missing_captured_solid_is_observable_as_zero_solids(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = scaffold.model_path.read_text(encoding="utf-8").replace(
        "    scad.capture_result(value=final_solid)\n",
        "    final_part = scad.make_part_rpart(\n"
        "        part_id='part', body=final_solid\n"
        "    )\n"
        "    scad.capture_result(value=final_part)\n",
    )
    source = source.replace("    return final_solid\n", "    return final_part\n")
    source = source.replace(
        "if not isinstance(FINAL_SOLID, scad.Solid):\n"
        '    raise TypeError("Model Source must return one SimpleCADAPI Solid")\n',
        "",
    )
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.captured_solid_count == 0
    assert "captured 0 Solids" in (result.error or "")


def test_unexpected_artifact_member_is_not_a_validated_result(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = scaffold.model_path.read_text(encoding="utf-8")
    source += (
        "\n(ARTIFACT_DIR / 'unexpected.log').write_text('debug', encoding='utf-8')\n"
    )
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.artifact_entries == ("model.scene.zip", "unexpected.log")
    assert result.scene_parse_result.valid is True
    assert "only artifacts/model.scene.zip" in (result.error or "")


def test_timeout_stops_model_process(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import time\ntime.sleep(5)\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=0.1)

    assert result.status == "timed_out"
    assert result.exit_code is not None
    assert "timed out" in (result.error or "").lower()


def test_external_cancellation_terminates_model_process(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import time\ntime.sleep(10)\n",
        encoding="utf-8",
    )
    token = CancellationToken()
    results: list[object] = []

    worker = threading.Thread(
        target=lambda: results.append(
            CADExecutor().execute(
                tmp_path,
                timeout_seconds=10.0,
                cancellation_token=token,
            )
        )
    )
    worker.start()
    time.sleep(0.2)
    token.cancel()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert len(results) == 1
    assert results[0].status == "cancelled"
    assert results[0].process_id is not None
    assert "cancel" in (results[0].error or "").lower()


def test_output_is_bounded_and_credential_like_text_is_redacted(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import sys\n"
        "sys.stdout.write('OPENAI_API_KEY=sk-test-secret-value\\n' + 'x' * 1000)\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, max_output_bytes=128, timeout_seconds=5.0)

    assert result.status == "failed"
    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 128
    assert "sk-test-secret-value" not in result.stdout
    assert "OPENAI_API_KEY=sk-test-secret-value" not in (result.error or "")
    assert "[REDACTED]" in result.stdout


def test_cad_environment_removes_provider_credentials_but_keeps_runtime_values() -> (
    None
):
    environment = build_cad_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_BASE_URL": "https://provider.invalid/v1",
            "MODEL_API_ENDPOINT": "https://provider.invalid/model",
            "LANGCHAIN_API_KEY": "lang-secret",
            "PATH": "/usr/bin",
            "SAFE_RUNTIME_VALUE": "kept",
        }
    )

    assert "OPENAI_API_KEY" not in environment
    assert "OPENAI_BASE_URL" not in environment
    assert "MODEL_API_ENDPOINT" not in environment
    assert "LANGCHAIN_API_KEY" not in environment
    assert environment["PATH"] == "/usr/bin"
    assert environment["SAFE_RUNTIME_VALUE"] == "kept"
