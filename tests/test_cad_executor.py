from __future__ import annotations

import json
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
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.final_shape_count == 1
    assert result.solid_count == 1
    assert result.solid_volume is not None
    assert math.isfinite(result.solid_volume) and result.solid_volume > 0
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is True
    assert result.scene_parse_result.glb_asset_count == 2
    assert result.scene_parse_result.model_json_present is False
    assert result.artifact_entries == ("model.scene.zip",)
    assert result.preflight_status == "passed"
    assert result.error_type is None
    assert "cadflow" in result.imported_modules


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
    assert result.error_type == "execution"


def test_syntax_error_is_reported_by_preflight_without_starting_cad(
    tmp_path: Path,
) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "def build_model(model):\n    return (\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=5.0)

    assert result.status == "failed"
    assert result.preflight_status == "failed"
    assert result.error_type == "syntax"
    assert result.process_id is None
    assert result.error_location is not None
    assert "model.py" in result.error_location


def test_import_error_is_classified_after_source_preflight(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import package_that_does_not_exist\n\n"
        "def build_model(model):\n    raise AssertionError('unreachable')\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=5.0)

    assert result.status == "failed"
    assert result.preflight_status == "failed"
    assert result.error_type == "import"
    assert result.error_location is not None


def test_api_error_in_model_source_is_classified_with_location(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    return model.not_a_cadflow_method()
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.preflight_status == "passed"
    assert result.error_type == "api"
    assert result.error_location is not None
    assert "model.py" in result.error_location


def test_invalid_scene_artifact_is_not_promoted_to_success(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """from pathlib import Path
import cadflow as cad

def build_model(model: cad.Model):
    def invalid_export_scene(*, package, path):
        Path(path).write_bytes(b'not a Scene ZIP')
    cad.export_scene = invalid_export_scene
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.final_shape_count == 1
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is False
    assert "zip" in (result.error or "").lower()


def test_final_shape_count_must_be_exactly_one(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = """import cadflow as cad

def build_model(model: cad.Model):
    return [model.box(width=10.0, depth=10.0, height=10.0)]
"""
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.final_shape_count is None
    assert "CadFlow Shape" in (result.error or "")


def test_non_solid_return_is_observable_as_failure(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = """import cadflow as cad

def build_model(model: cad.Model):
    return model.polyline(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
"""
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.final_shape_count is None
    assert "solid-compatible" in (result.error or "")


def test_unexpected_artifact_member_is_not_a_validated_result(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """from pathlib import Path
import cadflow as cad

PROJECT_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = PROJECT_DIR / "artifacts"

def build_model(model: cad.Model):
    (ARTIFACT_DIR / "unexpected.log").write_text("debug", encoding="utf-8")
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
        encoding="utf-8",
    )

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


def test_major_cad_operations_emit_mesh_preview_frames(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    left = model.box(width=20.0, depth=20.0, height=10.0)
    right = model.box(width=20.0, depth=20.0, height=10.0)
    joined = model.union(left, right)
    tool = model.cylinder(radius=3.0, height=20.0)
    return model.cut(joined, tool)
""",
        encoding="utf-8",
    )
    frames = []

    result = CADExecutor().execute(
        tmp_path,
        timeout_seconds=30.0,
        attempt=2,
        preview_callback=frames.append,
    )

    assert result.status == "succeeded"
    assert [frame.operation for frame in frames] == ["union", "cut"]
    assert [frame.revision for frame in frames] == [1, 2]
    for frame in frames:
        document = json.loads(frame.path.read_text(encoding="utf-8"))
        assert document["operation"] == frame.operation
        assert document["vertices"]
        assert document["triangles"]
    assert result.artifact_entries == ("model.scene.zip",)


def test_preview_callback_failure_does_not_fail_cad_execution(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
        encoding="utf-8",
    )

    def broken_callback(_frame: object) -> None:
        raise RuntimeError("viewer unavailable")

    result = CADExecutor().execute(
        tmp_path,
        timeout_seconds=30.0,
        preview_callback=broken_callback,
    )

    assert result.status == "succeeded"
    assert result.artifact_entries == ("model.scene.zip",)
