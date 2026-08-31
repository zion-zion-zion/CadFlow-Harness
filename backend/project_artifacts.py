"""Versioned CAD artifact persistence used by ``ProjectStore``.

This module owns artifact directories and acceptance evidence, but intentionally
does not create a lock.  Callers must hold the ProjectStore lock while invoking
mutating operations.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping

from .model_source import ARTIFACT_DIRECTORY_NAME, CODE_DIRECTORY_NAME
from .product_artifact import (
    PRODUCT_ARTIFACT_MANIFEST_NAME,
    ProductArtifact,
    ProductArtifactError,
    ProductArtifactStatus,
    accept_product_artifact,
    load_product_artifact,
)
from .scene_validation import validate_scene_artifact


CURRENT_ARTIFACT_NAME = "current.json"
DEFAULT_ARTIFACT_VERSION_LIMIT = 10
_ARTIFACT_VERSION_PATTERN = re.compile(r"^v([0-9]{4,})$")


class ArtifactAcceptanceError(ValueError):
    """Raised when final scene/review evidence is not sufficient for acceptance."""


class ArtifactVersionStore:
    """Read and mutate one Project's versioned artifact tree."""

    def __init__(self, project_dir: Path, *, version_limit: int) -> None:
        self.project_dir = project_dir
        self.version_limit = version_limit

    @property
    def artifact_dir(self) -> Path:
        return self.project_dir / ARTIFACT_DIRECTORY_NAME

    def current_version(self) -> int | None:
        path = self.project_dir / CURRENT_ARTIFACT_NAME
        if not path.is_file() or path.is_symlink():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        version = payload.get("version") if isinstance(payload, Mapping) else None
        return version if isinstance(version, int) and not isinstance(version, bool) else None

    def current_result_kind(self) -> str | None:
        path = self.project_dir / CURRENT_ARTIFACT_NAME
        if not path.is_file() or path.is_symlink():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        result_kind = payload.get("result_kind")
        if result_kind in {"part", "assembly"}:
            return str(result_kind)
        version = payload.get("version")
        return "part" if isinstance(version, int) and not isinstance(version, bool) else None

    def versions(self) -> list[int]:
        versions: list[int] = []
        if not self.artifact_dir.is_dir():
            return versions
        for child in self.artifact_dir.iterdir():
            match = _ARTIFACT_VERSION_PATTERN.fullmatch(child.name)
            if child.is_dir() and match:
                versions.append(int(match.group(1)))
        return sorted(versions)

    def commit(
        self,
        *,
        scene_evidence: Mapping[str, Any],
        review_evidence: Mapping[str, Any],
    ) -> int:
        """Promote the unversioned Draft bundle and atomically publish its version."""

        versions = self.versions()
        version = (versions[-1] if versions else 0) + 1
        target = self.artifact_dir / f"v{version:04d}"
        temporary = self.artifact_dir / f".v{version:04d}.{uuid.uuid4().hex}.tmp"
        files_dir = temporary / "files"
        source_dir = temporary / "source"
        files_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)
        for child in self.artifact_dir.iterdir():
            if child == temporary or _ARTIFACT_VERSION_PATTERN.fullmatch(child.name):
                continue
            destination = files_dir / child.name
            if child.is_symlink():
                continue
            if child.is_dir():
                shutil.copytree(child, destination)
            elif child.is_file():
                shutil.copy2(child, destination)

        accepted = accept_product_artifact(
            files_dir,
            scene_evidence=scene_evidence,
            review_evidence=review_evidence,
        )

        def versioned_path(path: Path) -> str:
            relative = path.relative_to(files_dir).as_posix()
            return f"{ARTIFACT_DIRECTORY_NAME}/v{version:04d}/files/{relative}"

        manifest = {
            "schema_version": "cadflow-project-artifact/v1",
            "version": version,
            "created_at": _timestamp(),
            "status": ProductArtifactStatus.ACCEPTED.value,
            "result_kind": accepted.result_kind,
            "product_manifest": versioned_path(
                files_dir / PRODUCT_ARTIFACT_MANIFEST_NAME
            ),
            "scene": versioned_path(accepted.file_path("scene")),
            "product_step": versioned_path(accepted.file_path("product_step")),
            "bom": versioned_path(accepted.file_path("bom")),
            "validation_report": versioned_path(
                accepted.file_path("validation_report")
            ),
        }
        _write_json(temporary / "manifest.json", manifest)
        self._snapshot_source(source_dir)
        temporary.replace(target)
        _write_json(self.project_dir / CURRENT_ARTIFACT_NAME, manifest)
        self.prune(current=version)
        return version

    def _snapshot_source(self, source_dir: Path) -> None:
        code_dir = self.project_dir / CODE_DIRECTORY_NAME
        if code_dir.is_symlink():
            raise ValueError("Project code directory must not be a symlink")
        if code_dir.exists() and not code_dir.is_dir():
            raise ValueError("Project code directory must be a directory")
        if code_dir.is_dir():
            for source in code_dir.rglob("*.py"):
                relative = source.relative_to(code_dir)
                if source.is_symlink():
                    continue
                destination = source_dir / CODE_DIRECTORY_NAME / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def prune(self, *, current: int) -> None:
        versions = self.versions()
        removable = versions[: max(0, len(versions) - self.version_limit)]
        for version in removable:
            if version != current:
                shutil.rmtree(self.artifact_dir / f"v{version:04d}")

    def restore_current(self) -> None:
        version = self.current_version()
        if version is None:
            return
        version_dir = self.artifact_dir / f"v{version:04d}"
        files_dir = version_dir / "files"
        source_dir = version_dir / "source"
        if files_dir.is_dir():
            for source in files_dir.iterdir():
                destination = self.artifact_dir / source.name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
        if source_dir.is_dir():
            saved_code_dir = source_dir / CODE_DIRECTORY_NAME
            # Versions written before the code/ layout stored model.py directly
            # under source/. Treat those files as legacy source snapshots.
            source_root = saved_code_dir if saved_code_dir.is_dir() else source_dir
            saved_sources = {
                source.relative_to(source_root)
                for source in source_root.rglob("*.py")
                if source.is_file() and not source.is_symlink()
            }
            current_code_dir = self.project_dir / CODE_DIRECTORY_NAME
            if current_code_dir.is_symlink():
                current_code_dir.unlink()
            current_code_dir.mkdir(parents=True, exist_ok=True)
            for source in current_code_dir.rglob("*.py"):
                relative = source.relative_to(current_code_dir)
                if source.is_symlink() or relative not in saved_sources:
                    source.unlink()
            for source in source_root.rglob("*.py"):
                relative = source.relative_to(source_root)
                destination = current_code_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def discard_unvalidated(self) -> None:
        if self.artifact_dir.is_symlink():
            self.artifact_dir.unlink()
            return
        if not self.artifact_dir.is_dir():
            return
        for child in self.artifact_dir.iterdir():
            if _ARTIFACT_VERSION_PATTERN.fullmatch(child.name):
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.restore_current()

    def product(self) -> ProductArtifact:
        version = self.current_version()
        if version is None:
            raise ValueError("Project has no versioned Product Artifact")
        bundle_root = self.artifact_dir / f"v{version:04d}" / "files"
        try:
            artifact = load_product_artifact(bundle_root)
        except (OSError, ProductArtifactError) as exc:
            raise ValueError("versioned Product Artifact is invalid") from exc
        if artifact.status is not ProductArtifactStatus.ACCEPTED:
            raise ValueError("versioned Product Artifact is not Accepted")
        return artifact

    def scene(self) -> Path:
        version = self.current_version()
        if version is not None:
            try:
                return self.product().file_path("scene")
            except (ProductArtifactError, ValueError):
                pass
        artifact = (
            self.artifact_dir / f"v{version:04d}" / "files" / "model.scene.zip"
            if version is not None
            else self.artifact_dir / "model.scene.zip"
        )
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError("Succeeded Project has no canonical Scene Artifact")
        return artifact


def acceptance_evidence(
    candidate: ProductArtifact,
    diagnostics: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate execution, Scene, and independent review evidence for one Draft."""

    if not isinstance(diagnostics, Mapping):
        raise ArtifactAcceptanceError("Accepted product requires final run diagnostics")
    execution = diagnostics.get("execution_result")
    if not isinstance(execution, Mapping):
        raise ArtifactAcceptanceError("Accepted product requires execution diagnostics")
    if (
        execution.get("status") != "succeeded"
        or execution.get("exit_code") != 0
        or execution.get("result_kind") != candidate.result_kind
        or execution.get("product_manifest_path") != "artifacts/product.json"
        or execution.get("product_status") != ProductArtifactStatus.DRAFT.value
        or execution.get("product_validation_status") != "Passed"
        or execution.get("product_validation_failures") != []
    ):
        raise ArtifactAcceptanceError(
            "product execution did not pass deterministic validation"
        )
    summary = candidate.summary
    expected_counts = {
        "component_count": summary.component_count,
        "leaf_part_count": summary.leaf_part_count,
        "unique_part_count": summary.unique_part_count,
        "solid_count": summary.solid_count,
    }
    if any(execution.get(name) != value for name, value in expected_counts.items()):
        raise ArtifactAcceptanceError("execution diagnostics do not match product structure")
    reported_volume = execution.get("solid_volume")
    if (
        not isinstance(reported_volume, (int, float))
        or isinstance(reported_volume, bool)
        or not math.isclose(
            float(reported_volume), summary.volume_mm3, rel_tol=1e-12, abs_tol=1e-12
        )
    ):
        raise ArtifactAcceptanceError("execution diagnostics do not match product volume")

    scene_report = validate_scene_artifact(candidate.file_path("scene"))
    reported_scene = execution.get("scene_parse_result")
    if (
        not scene_report.valid
        or not isinstance(reported_scene, Mapping)
        or reported_scene.get("valid") is not True
    ):
        raise ArtifactAcceptanceError("Accepted product requires a valid parsed Scene")

    review = diagnostics.get("review_result")
    if not isinstance(review, Mapping) or review.get("status") != "pass":
        raise ArtifactAcceptanceError("Accepted product requires a passing CAD review")
    execution_source_hash = execution.get("review_model_sha256")
    if (
        not isinstance(execution_source_hash, str)
        or not execution_source_hash
        or review.get("model_sha256") != execution_source_hash
    ):
        raise ArtifactAcceptanceError(
            "CAD review is not bound to the executed source revision"
        )
    findings = review.get("findings", [])
    if not isinstance(findings, list) or any(
        isinstance(item, Mapping)
        and item.get("severity") in {"blocking", "major"}
        for item in findings
    ):
        raise ArtifactAcceptanceError("passing CAD review contains blocking findings")
    review_evidence = {
        key: review.get(key)
        for key in (
            "status",
            "summary",
            "model_sha256",
            "reviewer_version",
            "checked_requirements",
            "evidence_hashes",
        )
    }
    return scene_report.to_dict(), review_evidence


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "ArtifactAcceptanceError",
    "ArtifactVersionStore",
    "CURRENT_ARTIFACT_NAME",
    "DEFAULT_ARTIFACT_VERSION_LIMIT",
    "acceptance_evidence",
]
