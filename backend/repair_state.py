"""Durable, request-bound state for progressive CAD repair runs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .cad_executor import ExecutionResult
from .cad_review import ReviewResult
from .failure_packet import (
    FailurePacket,
    FailureType,
    normalize_execution_failure,
    normalize_review_failure,
)


REPAIR_STATE_DIRECTORY_NAME = ".agent-state"
DESIGN_CONTRACTS_NAME = "design-contracts.jsonl"
ATTEMPT_LEDGER_NAME = "attempt-ledger.jsonl"
LAST_PASSING_SOURCE_NAME = "last-passing-source.zip"
LAST_PASSING_SOURCE_METADATA_NAME = "last-passing-source.json"
DESIGN_CONTRACT_SCHEMA_VERSION = "cadflow-design-contract/v1"
ATTEMPT_SCHEMA_VERSION = "cadflow-attempt/v1"
LAST_PASSING_SOURCE_SCHEMA_VERSION = "cadflow-last-passing-source/v1"
ALLOWED_TASK_TYPES = frozenset(
    {"single_part", "assembly", "modify_part", "modify_assembly"}
)
MAX_CONTRACT_ITEMS = 100
MAX_CONTRACT_ITEM_CHARS = 2_000
_STATE_LOCKS_GUARD = threading.Lock()
_STATE_LOCKS: dict[Path, threading.RLock] = {}


class RepairStateError(ValueError):
    """Raised when persisted progressive-repair state is invalid."""


class DesignContractError(RepairStateError):
    """Raised when a Design Contract cannot be accepted by the host."""


@dataclass(frozen=True)
class RunIdentity:
    """Host-owned identity of one user request within a Project turn."""

    project_id: str
    turn_id: str
    request_id: str
    request_text: str

    def __post_init__(self) -> None:
        for name in ("project_id", "turn_id", "request_id", "request_text"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RepairStateError(f"Run identity {name} must not be empty")

    @property
    def request_sha256(self) -> str:
        return hashlib.sha256(self.request_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DesignContract:
    """Validated design intent submitted before the first CAD validation."""

    contract_id: str
    project_id: str
    turn_id: str
    request_id: str
    request_text: str
    request_sha256: str
    task_type: str
    explicit_requirements: tuple[str, ...]
    key_components: tuple[str, ...]
    assumptions: tuple[str, ...]
    implementation_stages: tuple[str, ...]
    created_at: str
    schema_version: str = DESIGN_CONTRACT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "project_id": self.project_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "request_text": self.request_text,
            "request_sha256": self.request_sha256,
            "task_type": self.task_type,
            "explicit_requirements": list(self.explicit_requirements),
            "key_components": list(self.key_components),
            "assumptions": list(self.assumptions),
            "implementation_stages": list(self.implementation_stages),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DesignContract":
        if value.get("schema_version") != DESIGN_CONTRACT_SCHEMA_VERSION:
            raise RepairStateError("unsupported Design Contract schema version")
        required = {
            name: _required_text(value, name)
            for name in (
                "contract_id",
                "project_id",
                "turn_id",
                "request_id",
                "request_text",
                "request_sha256",
                "task_type",
                "created_at",
            )
        }
        contract = cls(
            **required,
            explicit_requirements=_validated_items(
                "explicit_requirements", value.get("explicit_requirements"), required=True
            ),
            key_components=_validated_items(
                "key_components", value.get("key_components"), required=True
            ),
            assumptions=_validated_items(
                "assumptions", value.get("assumptions"), required=False
            ),
            implementation_stages=_validated_items(
                "implementation_stages",
                value.get("implementation_stages"),
                required=True,
            ),
        )
        if contract.task_type not in ALLOWED_TASK_TYPES:
            raise RepairStateError("Design Contract task type is invalid")
        expected_request_hash = hashlib.sha256(
            contract.request_text.encode("utf-8")
        ).hexdigest()
        if contract.request_sha256 != expected_request_hash:
            raise RepairStateError("Design Contract request hash does not match")
        expected_contract_id = _design_contract_id(
            project_id=contract.project_id,
            turn_id=contract.turn_id,
            request_id=contract.request_id,
            request_sha256=contract.request_sha256,
            task_type=contract.task_type,
            explicit_requirements=contract.explicit_requirements,
            key_components=contract.key_components,
            assumptions=contract.assumptions,
            implementation_stages=contract.implementation_stages,
        )
        if contract.contract_id != expected_contract_id:
            raise RepairStateError("Design Contract content hash does not match")
        return contract


@dataclass(frozen=True)
class AttemptSignals:
    """Host-detected repair patterns for one validation or Review attempt."""

    repeated_failure: bool = False
    oscillation: bool = False
    broken_conditions: tuple[str, ...] = ()
    regression_from_last_passing: bool = False
    last_passing_revision: str | None = None
    changed_from_last_passing: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "repeated_failure": self.repeated_failure,
            "oscillation": self.oscillation,
            "broken_conditions": list(self.broken_conditions),
            "regression_from_last_passing": self.regression_from_last_passing,
            "last_passing_revision": self.last_passing_revision,
            "changed_from_last_passing": list(self.changed_from_last_passing),
            "hints": list(self.hints),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AttemptSignals":
        if not isinstance(value, Mapping):
            raise RepairStateError("Attempt signals must be an object")
        return cls(
            repeated_failure=value.get("repeated_failure") is True,
            oscillation=value.get("oscillation") is True,
            broken_conditions=_string_tuple(value.get("broken_conditions")),
            regression_from_last_passing=(
                value.get("regression_from_last_passing") is True
            ),
            last_passing_revision=_optional_text(value.get("last_passing_revision")),
            changed_from_last_passing=_string_tuple(
                value.get("changed_from_last_passing")
            ),
            hints=_string_tuple(value.get("hints")),
        )


@dataclass(frozen=True)
class AttemptRecord:
    """One durable, request-bound validation or CAD Review attempt."""

    sequence: int
    attempt_index: int
    attempt_kind: str
    project_id: str
    turn_id: str
    request_id: str
    request_text: str
    request_sha256: str
    design_contract: DesignContract
    source_revision: str
    source_files: Mapping[str, str]
    changed_files: tuple[str, ...]
    result_status: str
    validation_status: str | None
    review_status: str | None
    failure_type: FailureType | None
    failure_signature: str | None
    failure_packet: FailurePacket | None
    passed_conditions: tuple[str, ...]
    signals: AttemptSignals
    result_summary: Mapping[str, Any]
    created_at: str
    schema_version: str = ATTEMPT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "attempt_index": self.attempt_index,
            "attempt_kind": self.attempt_kind,
            "project_id": self.project_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "request_text": self.request_text,
            "request_sha256": self.request_sha256,
            "design_contract": self.design_contract.to_dict(),
            "source_revision": self.source_revision,
            "source_files": dict(self.source_files),
            "changed_files": list(self.changed_files),
            "result_status": self.result_status,
            "validation_status": self.validation_status,
            "review_status": self.review_status,
            "failure_type": self.failure_type.value if self.failure_type else None,
            "failure_signature": self.failure_signature,
            "failure_packet": (
                self.failure_packet.to_dict() if self.failure_packet else None
            ),
            "passed_conditions": list(self.passed_conditions),
            "signals": self.signals.to_dict(),
            "result_summary": dict(self.result_summary),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttemptRecord":
        if value.get("schema_version") != ATTEMPT_SCHEMA_VERSION:
            raise RepairStateError("unsupported Attempt Ledger schema version")
        raw_contract = value.get("design_contract")
        if not isinstance(raw_contract, Mapping):
            raise RepairStateError("Attempt Design Contract is invalid")
        raw_source_files = value.get("source_files")
        if not isinstance(raw_source_files, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in raw_source_files.items()
        ):
            raise RepairStateError("Attempt source file manifest is invalid")
        raw_packet = value.get("failure_packet")
        packet = _failure_packet_from_dict(raw_packet)
        raw_summary = value.get("result_summary")
        if not isinstance(raw_summary, Mapping):
            raise RepairStateError("Attempt result summary is invalid")
        failure_type_value = value.get("failure_type")
        try:
            failure_type = (
                FailureType(failure_type_value)
                if isinstance(failure_type_value, str)
                else None
            )
        except ValueError as exc:
            raise RepairStateError("Attempt failure type is invalid") from exc
        return cls(
            sequence=_positive_int(value, "sequence"),
            attempt_index=_positive_int(value, "attempt_index"),
            attempt_kind=_required_text(value, "attempt_kind"),
            project_id=_required_text(value, "project_id"),
            turn_id=_required_text(value, "turn_id"),
            request_id=_required_text(value, "request_id"),
            request_text=_required_text(value, "request_text"),
            request_sha256=_required_text(value, "request_sha256"),
            design_contract=DesignContract.from_dict(raw_contract),
            source_revision=_required_text(value, "source_revision"),
            source_files=dict(raw_source_files),
            changed_files=_string_tuple(value.get("changed_files")),
            result_status=_required_text(value, "result_status"),
            validation_status=_optional_text(value.get("validation_status")),
            review_status=_optional_text(value.get("review_status")),
            failure_type=failure_type,
            failure_signature=_optional_text(value.get("failure_signature")),
            failure_packet=packet,
            passed_conditions=_string_tuple(value.get("passed_conditions")),
            signals=AttemptSignals.from_dict(value.get("signals")),
            result_summary=dict(raw_summary),
            created_at=_required_text(value, "created_at"),
        )


@dataclass(frozen=True)
class LastPassingSource:
    """Verified metadata and archive path for the latest deterministic pass."""

    project_id: str
    turn_id: str
    request_id: str
    source_revision: str
    source_files: Mapping[str, str]
    attempt_sequence: int
    archive_sha256: str
    archive_path: Path
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return safe metadata without exposing the host filesystem path."""

        return {
            "schema_version": LAST_PASSING_SOURCE_SCHEMA_VERSION,
            "project_id": self.project_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "source_revision": self.source_revision,
            "source_files": dict(self.source_files),
            "attempt_sequence": self.attempt_sequence,
            "archive_sha256": self.archive_sha256,
            "created_at": self.created_at,
        }


class ProjectRepairState:
    """Persist repair context behind one small Project-local interface."""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.project_id = self.project_dir.name
        self.state_dir = self.project_dir / REPAIR_STATE_DIRECTORY_NAME
        self.contracts_path = self.state_dir / DESIGN_CONTRACTS_NAME
        self.attempts_path = self.state_dir / ATTEMPT_LEDGER_NAME
        self.last_passing_archive_path = self.state_dir / LAST_PASSING_SOURCE_NAME
        self.last_passing_metadata_path = (
            self.state_dir / LAST_PASSING_SOURCE_METADATA_NAME
        )
        self._lock = _state_lock(self.state_dir)

    def submit_design_contract(
        self,
        identity: RunIdentity,
        *,
        task_type: str,
        explicit_requirements: object,
        key_components: object,
        assumptions: object,
        implementation_stages: object,
    ) -> DesignContract:
        """Validate and durably bind one contract to the current request."""

        self._require_project(identity)
        normalized_task_type = _required_value("task_type", task_type)
        if normalized_task_type not in ALLOWED_TASK_TYPES:
            allowed = ", ".join(sorted(ALLOWED_TASK_TYPES))
            raise DesignContractError(f"task_type must be one of: {allowed}")
        fields = {
            "task_type": normalized_task_type,
            "explicit_requirements": list(
                _validated_items(
                    "explicit_requirements", explicit_requirements, required=True
                )
            ),
            "key_components": list(
                _validated_items("key_components", key_components, required=True)
            ),
            "assumptions": list(
                _validated_items("assumptions", assumptions, required=False)
            ),
            "implementation_stages": list(
                _validated_items(
                    "implementation_stages", implementation_stages, required=True
                )
            ),
        }
        contract_id = _design_contract_id(
            project_id=identity.project_id,
            turn_id=identity.turn_id,
            request_id=identity.request_id,
            request_sha256=identity.request_sha256,
            task_type=normalized_task_type,
            explicit_requirements=tuple(fields["explicit_requirements"]),
            key_components=tuple(fields["key_components"]),
            assumptions=tuple(fields["assumptions"]),
            implementation_stages=tuple(fields["implementation_stages"]),
        )
        contract = DesignContract(
            contract_id=contract_id,
            project_id=identity.project_id,
            turn_id=identity.turn_id,
            request_id=identity.request_id,
            request_text=identity.request_text,
            request_sha256=identity.request_sha256,
            task_type=normalized_task_type,
            explicit_requirements=tuple(fields["explicit_requirements"]),
            key_components=tuple(fields["key_components"]),
            assumptions=tuple(fields["assumptions"]),
            implementation_stages=tuple(fields["implementation_stages"]),
            created_at=_timestamp(),
        )
        with self._lock:
            existing = self.design_contract(identity)
            if existing is not None:
                if existing.contract_id == contract.contract_id:
                    return existing
                raise DesignContractError(
                    "Design Contract is already fixed for the current request"
                )
            _append_json(self.contracts_path, contract.to_dict())
        return contract

    def design_contract(self, identity: RunIdentity) -> DesignContract | None:
        """Return only a contract bound to this exact Project request."""

        self._require_project(identity)
        with self._lock:
            contracts = [
                DesignContract.from_dict(record)
                for record in _read_json_lines(self.contracts_path)
            ]
        for contract in reversed(contracts):
            if (
                contract.project_id == identity.project_id
                and contract.turn_id == identity.turn_id
                and contract.request_id == identity.request_id
                and contract.request_sha256 == identity.request_sha256
                and contract.request_text == identity.request_text
            ):
                return contract
        return None

    def attempts(self, identity: RunIdentity | None = None) -> tuple[AttemptRecord, ...]:
        """Return the durable Attempt Ledger, optionally for one exact request."""

        if identity is not None:
            self._require_project(identity)
        with self._lock:
            attempts = tuple(
                AttemptRecord.from_dict(record)
                for record in _read_json_lines(self.attempts_path)
            )
        if identity is None:
            return attempts
        return tuple(
            attempt
            for attempt in attempts
            if _attempt_matches(attempt, identity)
        )

    def record_validation(
        self,
        identity: RunIdentity,
        result: ExecutionResult,
    ) -> AttemptRecord:
        """Record one real deterministic validation and checkpoint each pass."""

        if not isinstance(result, ExecutionResult):
            raise RepairStateError("validation attempt requires an ExecutionResult")
        packet = normalize_execution_failure(result)
        passed_conditions = _execution_passed_conditions(result)
        result_summary: dict[str, Any] = {
            "status": result.status,
            "error_type": result.error_type,
            "result_kind": result.result_kind,
            "product_validation_status": result.product_validation_status,
            "scene_valid": result.scene_parse_result.valid,
        }
        return self._record_attempt(
            identity,
            attempt_kind="validation",
            result_status=result.status,
            validation_status=("passed" if result.is_validated_product else "failed"),
            review_status=None,
            packet=packet,
            passed_conditions=passed_conditions,
            failed_conditions=_failed_check_ids(result.product_validation_checks),
            result_summary=result_summary,
            checkpoint=result.is_validated_product,
        )

    def record_review(
        self,
        identity: RunIdentity,
        result: ReviewResult,
    ) -> AttemptRecord:
        """Record one independent CAD Review attempt against the current source."""

        if not isinstance(result, ReviewResult):
            raise RepairStateError("review attempt requires a ReviewResult")
        packet = normalize_review_failure(result)
        passed_conditions = (
            tuple(dict.fromkeys(result.checked_requirements))
            if result.status == "pass"
            else packet.preserve_conditions if packet else ()
        )
        return self._record_attempt(
            identity,
            attempt_kind="review",
            result_status=result.status,
            validation_status=None,
            review_status=result.status,
            packet=packet,
            passed_conditions=passed_conditions,
            failed_conditions=(),
            result_summary={
                "status": result.status,
                "summary": result.summary,
                "finding_categories": [
                    finding.category for finding in result.findings
                ],
            },
            checkpoint=False,
        )

    def last_passing_source(self) -> LastPassingSource | None:
        """Return the latest verified complete Python-source snapshot."""

        path = self.last_passing_metadata_path
        with self._lock:
            if not path.exists():
                return None
            if path.is_symlink() or not path.is_file():
                raise RepairStateError("Last Passing Source metadata is invalid")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RepairStateError("Last Passing Source metadata could not be read") from exc
            if not isinstance(value, Mapping) or value.get("schema_version") != (
                LAST_PASSING_SOURCE_SCHEMA_VERSION
            ):
                raise RepairStateError("Last Passing Source metadata is invalid")
            archive_path = self.last_passing_archive_path
            if archive_path.is_symlink() or not archive_path.is_file():
                raise RepairStateError("Last Passing Source archive is missing")
            archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            expected_hash = _required_text(value, "archive_sha256")
            if archive_hash != expected_hash:
                raise RepairStateError("Last Passing Source archive hash does not match")
            raw_source_files = value.get("source_files")
            if not isinstance(raw_source_files, Mapping) or any(
                not isinstance(key, str) or not isinstance(item, str)
                for key, item in raw_source_files.items()
            ):
                raise RepairStateError("Last Passing Source manifest is invalid")
            return LastPassingSource(
                project_id=_required_text(value, "project_id"),
                turn_id=_required_text(value, "turn_id"),
                request_id=_required_text(value, "request_id"),
                source_revision=_required_text(value, "source_revision"),
                source_files=dict(raw_source_files),
                attempt_sequence=_positive_int(value, "attempt_sequence"),
                archive_sha256=expected_hash,
                archive_path=archive_path,
                created_at=_required_text(value, "created_at"),
            )

    def _record_attempt(
        self,
        identity: RunIdentity,
        *,
        attempt_kind: str,
        result_status: str,
        validation_status: str | None,
        review_status: str | None,
        packet: FailurePacket | None,
        passed_conditions: tuple[str, ...],
        failed_conditions: tuple[str, ...],
        result_summary: Mapping[str, Any],
        checkpoint: bool,
    ) -> AttemptRecord:
        self._require_project(identity)
        with self._lock:
            contract = self.design_contract(identity)
            if contract is None:
                raise DesignContractError(
                    "A Design Contract bound to the current request is required"
                )
            source_revision, source_files, source_contents = _source_manifest(
                self.project_dir / "code"
            )
            all_attempts = self.attempts()
            request_attempts = tuple(
                attempt
                for attempt in all_attempts
                if _attempt_matches(attempt, identity)
            )
            previous = request_attempts[-1] if request_attempts else None
            last_passing = self.last_passing_source()
            signals = _attempt_signals(
                attempt_kind=attempt_kind,
                packet=packet,
                source_revision=source_revision,
                source_files=source_files,
                request_attempts=request_attempts,
                failed_conditions=failed_conditions,
                last_passing=last_passing,
            )
            record = AttemptRecord(
                sequence=len(all_attempts) + 1,
                attempt_index=(
                    sum(
                        attempt.attempt_kind == attempt_kind
                        for attempt in request_attempts
                    )
                    + 1
                ),
                attempt_kind=attempt_kind,
                project_id=identity.project_id,
                turn_id=identity.turn_id,
                request_id=identity.request_id,
                request_text=identity.request_text,
                request_sha256=identity.request_sha256,
                design_contract=contract,
                source_revision=source_revision,
                source_files=source_files,
                changed_files=_changed_files(
                    previous.source_files if previous else {}, source_files
                ),
                result_status=result_status,
                validation_status=validation_status,
                review_status=review_status,
                failure_type=packet.primary_type if packet else None,
                failure_signature=packet.failure_signature if packet else None,
                failure_packet=packet,
                passed_conditions=passed_conditions,
                signals=signals,
                result_summary=dict(result_summary),
                created_at=_timestamp(),
            )
            if checkpoint:
                self._write_last_passing_source(record, source_contents)
            _append_json(self.attempts_path, record.to_dict())
            return record

    def _write_last_passing_source(
        self,
        record: AttemptRecord,
        source_contents: Mapping[str, bytes],
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.state_dir.is_symlink():
            raise RepairStateError("repair state path must not be a symbolic link")
        temporary = self.last_passing_archive_path.with_name(
            f".{LAST_PASSING_SOURCE_NAME}.tmp"
        )
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for relative, content in sorted(source_contents.items()):
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, content)
            os.replace(temporary, self.last_passing_archive_path)
            archive_hash = hashlib.sha256(
                self.last_passing_archive_path.read_bytes()
            ).hexdigest()
            _write_json(
                self.last_passing_metadata_path,
                {
                    "schema_version": LAST_PASSING_SOURCE_SCHEMA_VERSION,
                    "project_id": record.project_id,
                    "turn_id": record.turn_id,
                    "request_id": record.request_id,
                    "source_revision": record.source_revision,
                    "source_files": dict(record.source_files),
                    "attempt_sequence": record.sequence,
                    "archive_sha256": archive_hash,
                    "created_at": record.created_at,
                },
            )
        except OSError as exc:
            raise RepairStateError("Last Passing Source could not be persisted") from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def _require_project(self, identity: RunIdentity) -> None:
        if identity.project_id != self.project_id:
            raise RepairStateError("Run identity does not belong to this Project")


def _validated_items(name: str, value: object, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DesignContractError(f"{name} must be a list of strings")
    if required and not value:
        raise DesignContractError(f"{name} must not be empty")
    if len(value) > MAX_CONTRACT_ITEMS:
        raise DesignContractError(f"{name} contains too many items")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DesignContractError(f"{name} must contain non-empty strings")
        text = item.strip()
        if len(text) > MAX_CONTRACT_ITEM_CHARS:
            raise DesignContractError(f"{name} item is too long")
        normalized.append(text)
    return tuple(normalized)


def _design_contract_id(
    *,
    project_id: str,
    turn_id: str,
    request_id: str,
    request_sha256: str,
    task_type: str,
    explicit_requirements: tuple[str, ...],
    key_components: tuple[str, ...],
    assumptions: tuple[str, ...],
    implementation_stages: tuple[str, ...],
) -> str:
    payload = {
        "project_id": project_id,
        "turn_id": turn_id,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "task_type": task_type,
        "explicit_requirements": list(explicit_requirements),
        "key_components": list(key_components),
        "assumptions": list(assumptions),
        "implementation_stages": list(implementation_stages),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _required_value(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignContractError(f"{name} must not be empty")
    return value.strip()


def _required_text(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise RepairStateError(f"Design Contract field is invalid: {name}")
    return item


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _positive_int(value: Mapping[str, Any], name: str) -> int:
    item = value.get(name)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise RepairStateError(f"repair state field is invalid: {name}")
    return item


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RepairStateError("repair state string list is invalid")
    return tuple(value)


def _failure_packet_from_dict(value: object) -> FailurePacket | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RepairStateError("Attempt Failure Packet is invalid")
    try:
        primary_type = FailureType(value.get("primary_type"))
    except ValueError as exc:
        raise RepairStateError("Attempt Failure Packet type is invalid") from exc
    source_edit_allowed = value.get("source_edit_allowed")
    if not isinstance(source_edit_allowed, bool):
        raise RepairStateError("Attempt Failure Packet source policy is invalid")
    return FailurePacket(
        primary_type=primary_type,
        failure_signature=_required_text(value, "failure_signature"),
        summary=_required_text(value, "summary"),
        key_evidence=_string_tuple(value.get("key_evidence")),
        source_scope=_string_tuple(value.get("source_scope")),
        preserve_conditions=_string_tuple(value.get("preserve_conditions")),
        suggested_action=_required_text(value, "suggested_action"),
        source_edit_allowed=source_edit_allowed,
    )


def _attempt_matches(attempt: AttemptRecord, identity: RunIdentity) -> bool:
    return bool(
        attempt.project_id == identity.project_id
        and attempt.turn_id == identity.turn_id
        and attempt.request_id == identity.request_id
        and attempt.request_sha256 == identity.request_sha256
        and attempt.request_text == identity.request_text
    )


def _source_manifest(
    code_dir: Path,
) -> tuple[str, dict[str, str], dict[str, bytes]]:
    if code_dir.is_symlink() or not code_dir.is_dir():
        raise RepairStateError("Project code directory is invalid")
    digest = hashlib.sha256()
    files: dict[str, str] = {}
    contents: dict[str, bytes] = {}
    for source_path in sorted(code_dir.rglob("*.py")):
        if source_path.is_symlink() or not source_path.is_file():
            raise RepairStateError("Project Python source must be a regular file")
        relative = source_path.relative_to(code_dir).as_posix()
        data = source_path.read_bytes()
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        key = f"code/{relative}"
        files[key] = hashlib.sha256(data).hexdigest()
        contents[key] = data
    return digest.hexdigest(), files, contents


def _changed_files(
    previous: Mapping[str, str], current: Mapping[str, str]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            path
            for path in set(previous).union(current)
            if previous.get(path) != current.get(path)
        )
    )


def _failed_check_ids(checks: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    return tuple(
        str(check["check_id"])
        for check in checks
        if check.get("status") == "failed" and isinstance(check.get("check_id"), str)
    )


def _execution_passed_conditions(result: ExecutionResult) -> tuple[str, ...]:
    conditions = [
        str(check["check_id"])
        for check in result.product_validation_checks
        if check.get("status") == "passed" and isinstance(check.get("check_id"), str)
    ]
    if result.solid_volume is not None and result.solid_volume > 0:
        conditions.append("positive_volume")
    if result.scene_parse_result.valid:
        conditions.append("scene_artifact")
    if result.is_validated_product:
        conditions.append("deterministic_validation")
    return tuple(dict.fromkeys(conditions))


def _attempt_signals(
    *,
    attempt_kind: str,
    packet: FailurePacket | None,
    source_revision: str,
    source_files: Mapping[str, str],
    request_attempts: tuple[AttemptRecord, ...],
    failed_conditions: tuple[str, ...],
    last_passing: LastPassingSource | None,
) -> AttemptSignals:
    comparable = tuple(
        attempt for attempt in request_attempts if attempt.attempt_kind == attempt_kind
    )
    previous = comparable[-1] if comparable else None
    signature = packet.failure_signature if packet else None
    repeated = bool(
        signature
        and previous is not None
        and previous.failure_signature == signature
    )
    earlier = comparable[:-1] if previous else comparable
    oscillation = bool(
        (signature and any(item.failure_signature == signature for item in earlier))
        or any(item.source_revision == source_revision for item in earlier)
    )
    previously_passed = {
        condition
        for attempt in request_attempts
        if attempt.attempt_kind == "validation"
        for condition in attempt.passed_conditions
    }
    broken = tuple(sorted(previously_passed.intersection(failed_conditions)))
    regression = bool(
        attempt_kind == "validation"
        and packet is not None
        and last_passing is not None
        and source_revision != last_passing.source_revision
    )
    changed_from_last = (
        _changed_files(last_passing.source_files, source_files)
        if regression and last_passing is not None
        else ()
    )
    hints: list[str] = []
    if repeated:
        hints.append(
            "The same stable failure repeated consecutively; make a different material repair."
        )
    if oscillation:
        hints.append(
            "The repair is oscillating between a previously seen source or failure state."
        )
    if broken:
        hints.append(
            "This change broke previously passed conditions: " + ", ".join(broken)
        )
    if regression:
        hints.append(
            "The current source regressed from Last Passing Source; preserve the "
            "passing baseline while repairing changed files: "
            + ", ".join(changed_from_last)
        )
    return AttemptSignals(
        repeated_failure=repeated,
        oscillation=oscillation,
        broken_conditions=broken,
        regression_from_last_passing=regression,
        last_passing_revision=(
            last_passing.source_revision if last_passing is not None else None
        ),
        changed_from_last_passing=changed_from_last,
        hints=tuple(hints),
    )


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise RepairStateError("repair state must be a regular file")
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RepairStateError("repair state records must be objects")
            records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepairStateError("repair state could not be read") from exc
    return records


def _append_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or (path.exists() and path.is_symlink()):
        raise RepairStateError("repair state path must not be a symbolic link")
    line = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise RepairStateError("repair state could not be persisted") from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or (path.exists() and path.is_symlink()):
        raise RepairStateError("repair state path must not be a symbolic link")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise RepairStateError("repair state could not be persisted") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _state_lock(path: Path) -> threading.RLock:
    key = path.resolve()
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, threading.RLock())


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "ALLOWED_TASK_TYPES",
    "AttemptRecord",
    "AttemptSignals",
    "DesignContract",
    "DesignContractError",
    "LastPassingSource",
    "ProjectRepairState",
    "RepairStateError",
    "RunIdentity",
]
