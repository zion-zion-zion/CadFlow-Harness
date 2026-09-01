"""CadFlow runner protocol decoding and source diagnostics."""

from __future__ import annotations

import json
import re
from pathlib import Path


RESULT_PREFIX = "__CADFLOW_EXECUTION_RESULT__"
PREFLIGHT_PREFIX = "__CADFLOW_PREFLIGHT__"
PHASE_PREFIX = "__CADFLOW_EXECUTION_PHASE__"
_TRACEBACK_LOCATION = re.compile(r'File "([^"]+)", line (\d+)')


def runner_payload(raw_stdout: bytes) -> dict[str, object] | None:
    text = raw_stdout.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def runner_preflight_payload(raw_stdout: bytes) -> dict[str, object] | None:
    text = raw_stdout.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith(PREFLIGHT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(PREFLIGHT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def runner_phase(raw_stdout: bytes) -> str | None:
    text = raw_stdout.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if not line.startswith(PHASE_PREFIX):
            continue
        try:
            payload = json.loads(line[len(PHASE_PREFIX) :])
        except json.JSONDecodeError:
            return None
        phase = payload.get("phase") if isinstance(payload, dict) else None
        return phase if isinstance(phase, str) and phase else None
    return None


def preflight_source(model_path: Path) -> tuple[str | None, str | None]:
    """Compile Model Source without executing it or creating a pyc file."""

    try:
        source = model_path.read_text(encoding="utf-8")
        compile(source, str(model_path), "exec")
    except SyntaxError as exc:
        location = f"{model_path}:{exc.lineno or 0}:{exc.offset or 0}"
        return f"SyntaxError: {exc.msg}", location
    except (OSError, UnicodeError) as exc:
        return str(exc), str(model_path)
    return None, None


def module_names(payload: dict[str, object]) -> tuple[str, ...]:
    values = payload.get("imported_modules")
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def looks_like_import_error(stderr: str, stdout: str) -> bool:
    text = f"{stderr}\n{stdout}"
    return "ModuleNotFoundError" in text or "ImportError" in text


def classify_process_error(stderr: str, stdout: str) -> str:
    text = f"{stderr}\n{stdout}"
    if "SyntaxError" in text:
        return "syntax"
    if "ModuleNotFoundError" in text or "ImportError" in text:
        return "import"
    if any(
        marker in text
        for marker in (
            "TypeError",
            "AttributeError",
            "NameError",
            "NotImplementedError",
        )
    ):
        return "api"
    return "execution"


def traceback_location(stderr: str, stdout: str) -> str | None:
    matches = _TRACEBACK_LOCATION.findall(f"{stderr}\n{stdout}")
    if not matches:
        return None
    path, line = matches[-1]
    return f"{path}:{line}"


def timeout_error(timeout_seconds: float, phase: str | None) -> str:
    base = f"CAD execution timed out after {timeout_seconds:g} seconds"
    if phase is None:
        return base
    guidance = {
        "model_build": (
            "simplify expensive booleans and build one representative Part first"
        ),
        "strict_constraint_solve": "simplify or repair the constraint graph",
        "product_step_export": "simplify product geometry before STEP export",
        "unique_part_step_export": "simplify the slow unique Part geometry",
        "step_export_replay": "simplify exported topology",
        "scene_export": "simplify render geometry",
        "review_evidence": "simplify the final product used for review renders",
    }.get(phase)
    message = f"{base} during {phase}"
    return f"{message}; {guidance}" if guidance else message


def shape_facts(
    payload: dict[str, object] | None,
) -> tuple[int | None, int | None, float | None]:
    if payload is None:
        return None, None, None
    count = payload.get("final_shape_count")
    solid_count = payload.get("solid_count")
    volume = payload.get("solid_volume")
    if not isinstance(count, int) or isinstance(count, bool):
        return None, None, None
    if not isinstance(solid_count, int) or isinstance(solid_count, bool):
        solid_count = None
    if not isinstance(volume, (int, float)) or isinstance(volume, bool):
        volume = None
    return count, solid_count, float(volume) if volume is not None else None


def result_kind(payload: dict[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("result_kind")
    return value if value in {"part", "assembly"} else None


def assembly_facts(payload: dict[str, object] | None) -> tuple[int | None, int | None]:
    if payload is None:
        return None, None
    values: list[int | None] = []
    for name in ("component_count", "leaf_part_count"):
        value = payload.get(name)
        values.append(
            value if isinstance(value, int) and not isinstance(value, bool) else None
        )
    return values[0], values[1]


def payload_string(payload: dict[str, object] | None, name: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(name)
    return value if isinstance(value, str) and value else None


def payload_int(payload: dict[str, object] | None, name: str) -> int | None:
    if payload is None:
        return None
    value = payload.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def payload_bool(payload: dict[str, object] | None, name: str) -> bool:
    if payload is None:
        return False
    value = payload.get(name)
    return value if isinstance(value, bool) else False


def payload_strings(payload: dict[str, object] | None, name: str) -> tuple[str, ...]:
    if payload is None:
        return ()
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return ()
    return tuple(value)


def review_facts(
    payload: dict[str, object] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Read bounded review evidence metadata emitted by the CAD child."""

    if payload is None:
        return None, None, None, None
    values = tuple(
        payload.get(name)
        for name in (
            "review_artifact_dir",
            "review_manifest_path",
            "review_model_sha256",
            "review_evidence_error",
        )
    )
    return tuple(
        value if isinstance(value, str) else None for value in values
    )  # type: ignore[return-value]


def first_error(stderr: str, stdout: str, fallback: str) -> str:
    for candidate in (stderr.strip(), stdout.strip()):
        if candidate:
            return candidate
    return fallback


# Compatibility aliases used by the stable ``backend.cad_executor`` facade.
_runner_payload = runner_payload
_runner_preflight_payload = runner_preflight_payload
_runner_phase = runner_phase
_preflight_source = preflight_source
_module_names = module_names
_looks_like_import_error = looks_like_import_error
_classify_process_error = classify_process_error
_traceback_location = traceback_location
_timeout_error = timeout_error
_shape_facts = shape_facts
_result_kind = result_kind
_assembly_facts = assembly_facts
_payload_string = payload_string
_payload_int = payload_int
_payload_bool = payload_bool
_payload_strings = payload_strings
_review_facts = review_facts
_first_error = first_error


__all__ = [
    "PHASE_PREFIX",
    "PREFLIGHT_PREFIX",
    "RESULT_PREFIX",
    "assembly_facts",
    "classify_process_error",
    "first_error",
    "looks_like_import_error",
    "module_names",
    "payload_bool",
    "payload_int",
    "payload_string",
    "payload_strings",
    "preflight_source",
    "result_kind",
    "review_facts",
    "runner_payload",
    "runner_phase",
    "runner_preflight_payload",
    "shape_facts",
    "timeout_error",
    "traceback_location",
]
