from __future__ import annotations

from backend import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_BYTES,
    CADExecutor,
    CancellationToken,
    ExecutionResult,
    build_cad_environment,
    redact_credentials,
)
from backend import cad_runner
from backend.cad_execution_contract import ExecutionResult as ContractResult
from backend.cad_process import (
    CAD_EXECUTION_TIMEOUT_SECONDS as ProcessTimeout,
    DEFAULT_OUTPUT_BYTES as ProcessOutputLimit,
)
from backend.cad_protocol import (
    PHASE_PREFIX,
    PREFLIGHT_PREFIX,
    RESULT_PREFIX,
)


def test_public_cad_execution_exports_keep_their_identity() -> None:
    assert ExecutionResult is ContractResult
    assert CAD_EXECUTION_TIMEOUT_SECONDS == ProcessTimeout
    assert DEFAULT_OUTPUT_BYTES == ProcessOutputLimit
    assert CADExecutor.__module__ == "backend.cad_executor"
    assert CancellationToken.__module__ == "backend.cad_process"
    assert callable(build_cad_environment)
    assert callable(redact_credentials)


def test_runner_protocol_literals_are_shared_by_host_and_child() -> None:
    assert RESULT_PREFIX == cad_runner.RESULT_PREFIX == "__CADFLOW_EXECUTION_RESULT__"
    assert PREFLIGHT_PREFIX == cad_runner.PREFLIGHT_PREFIX == "__CADFLOW_PREFLIGHT__"
    assert PHASE_PREFIX == cad_runner.PHASE_PREFIX == "__CADFLOW_EXECUTION_PHASE__"
