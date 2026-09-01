"""Host-side path, product, Scene, and review artifact validation."""

from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .cad_execution_contract import bounded_validation_checks
from .cad_protocol import payload_bool, payload_int, payload_string
from .product_artifact import (
    PRODUCT_ARTIFACT_MANIFEST_NAME,
    ProductArtifact,
    ProductArtifactError,
    load_product_artifact,
)
from .scene_validation import SceneParseResult, validate_scene_artifact


_ARTIFACT_VERSION_DIRECTORY = re.compile(r"^v[0-9]{4,}$")


@dataclass(frozen=True)
class HostArtifactFacts:
    artifact_entries: tuple[str, ...]
    scene_exists: bool
    scene_parse: SceneParseResult
    product_artifact: ProductArtifact | None
    product_artifact_error: str | None
    product_manifest_path: str | None
    product_status: str | None
    product_validation_checks: tuple[dict[str, object], ...]
    unique_part_count: int | None


@dataclass(frozen=True)
class ArtifactValidation:
    error: str | None = None
    error_type: str | None = None


def validate_project_paths(
    root: Path, code_dir: Path, model_path: Path, artifact_dir: Path
) -> None:
    if not root.is_dir():
        raise ValueError("Project working directory does not exist")
    if code_dir.is_symlink():
        raise ValueError("Project code directory must not be a symlink")
    if not code_dir.is_dir():
        raise ValueError("Project code directory does not exist")
    for source_path in code_dir.rglob("*"):
        if source_path.is_symlink():
            raise ValueError("Project code sources must not be symbolic links")
    if model_path.is_symlink():
        raise ValueError("Model Source must not be a symlink")
    if not _is_under(model_path.resolve(), code_dir):
        raise ValueError("Model Source is outside Project code")
    if not model_path.is_file():
        raise ValueError("current Project Model Source is missing")
    if artifact_dir.is_symlink():
        raise ValueError("Project artifact directory must not be a symlink")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    review_dir = root / ".cad-review"
    if review_dir.is_symlink():
        raise ValueError("Project CAD review directory must not be a symlink")
    if review_dir.exists() and not review_dir.is_dir():
        raise ValueError("Project CAD review path must be a directory")


def clear_artifacts(artifact_dir: Path) -> None:
    """Remove current-run artifacts while preserving accepted version folders."""

    for child in artifact_dir.iterdir():
        if child.is_dir() and _ARTIFACT_VERSION_DIRECTORY.fullmatch(child.name):
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def collect_artifact_facts(
    *,
    root: Path,
    artifact_dir: Path,
    scene_path: Path,
    payload: dict[str, object] | None,
    validation_short_circuited: bool,
) -> HostArtifactFacts:
    artifact_entries = artifact_entries_for(artifact_dir)
    scene_exists = scene_path.is_file() and not scene_path.is_symlink()
    scene_parse = (
        validate_scene_artifact(scene_path)
        if scene_exists
        else SceneParseResult(valid=False, error="canonical Scene Artifact is missing")
    )
    product_artifact: ProductArtifact | None = None
    product_artifact_error: str | None = None
    product_manifest_path: str | None = None
    product_status: str | None = None
    product_validation_checks: tuple[dict[str, object], ...] = ()
    reported_unique_part_count = payload_int(payload, "unique_part_count")
    unique_part_count: int | None = reported_unique_part_count
    if validation_short_circuited:
        product_status = payload_string(payload, "product_status")
        product_validation_checks = bounded_validation_checks(
            {"checks": payload.get("product_validation_checks", []) if payload else []}
        )
    else:
        try:
            product_artifact = load_product_artifact(artifact_dir)
            product_artifact.require_complete()
            product_manifest_path = str(
                (artifact_dir / PRODUCT_ARTIFACT_MANIFEST_NAME).relative_to(root)
            )
            product_status = product_artifact.status.value
            unique_part_count = product_artifact.summary.unique_part_count
            product_validation_checks = bounded_validation_checks(
                product_artifact.validation_report
            )
        except (OSError, ProductArtifactError) as exc:
            product_artifact_error = str(exc)
    return HostArtifactFacts(
        artifact_entries=artifact_entries,
        scene_exists=scene_exists,
        scene_parse=scene_parse,
        product_artifact=product_artifact,
        product_artifact_error=product_artifact_error,
        product_manifest_path=product_manifest_path,
        product_status=product_status,
        product_validation_checks=product_validation_checks,
        unique_part_count=unique_part_count,
    )


def validate_host_artifacts(
    *,
    root: Path,
    artifact_dir: Path,
    scene_path: Path,
    validation_short_circuited: bool,
    result_kind: str | None,
    component_count: int | None,
    leaf_part_count: int | None,
    solid_count: int | None,
    solid_volume: float | None,
    reported_product_manifest_path: str | None,
    reported_product_status: str | None,
    reported_unique_part_count: int | None,
    product_validation_status: str | None,
    product_validation_failures: tuple[str, ...],
    review_artifact_dir: str | None,
    review_manifest_path: str | None,
    review_evidence_error: str | None,
    facts: HostArtifactFacts,
) -> ArtifactValidation:
    """Reject child claims that disagree with on-disk artifacts or facts."""

    if validation_short_circuited:
        short_circuit_is_valid = bool(
            result_kind == "assembly"
            and product_validation_status == "Draft"
            and product_validation_failures
            and reported_product_status == "Draft"
            and reported_product_manifest_path is None
            and facts.unique_part_count is not None
            and leaf_part_count is not None
            and 1 <= facts.unique_part_count <= leaf_part_count
            and any(
                check.get("status") == "failed"
                for check in facts.product_validation_checks
            )
            and not facts.artifact_entries
            and not facts.scene_exists
            and review_artifact_dir is None
            and review_manifest_path is None
            and review_evidence_error is None
        )
        if not short_circuit_is_valid:
            return ArtifactValidation(
                "CAD process reported an invalid short-circuited Draft",
                "product_validation",
            )
        return ArtifactValidation()
    if facts.product_artifact_error is not None or facts.product_artifact is None:
        return ArtifactValidation(
            "product artifact could not be validated: "
            + (facts.product_artifact_error or "unknown product artifact error"),
            "product_artifact",
        )
    product_artifact = facts.product_artifact
    if product_artifact.result_kind != result_kind:
        return ArtifactValidation(
            "product artifact result kind does not match Model Source",
            "product_artifact",
        )
    if (
        product_artifact.summary.component_count != (component_count or 0)
        or product_artifact.summary.leaf_part_count
        != (leaf_part_count if result_kind == "assembly" else 1)
        or product_artifact.summary.solid_count != solid_count
        or solid_volume is None
        or not _volume_matches(product_artifact.summary.volume_mm3, solid_volume)
    ):
        return ArtifactValidation(
            "product artifact summary does not match observed CAD facts",
            "product_artifact",
        )
    if (
        reported_product_manifest_path != facts.product_manifest_path
        or reported_product_status != facts.product_status
        or reported_unique_part_count != facts.unique_part_count
    ):
        return ArtifactValidation(
            "CAD process product artifact report is inconsistent",
            "product_artifact",
        )
    if artifact_has_symlink(artifact_dir):
        return ArtifactValidation("artifacts must not contain symbolic links", "scene")
    if facts.artifact_entries != declared_artifact_entries(product_artifact):
        return ArtifactValidation(
            "artifacts contain files not declared by product.json", "product_artifact"
        )
    if product_artifact.file_path("scene") != scene_path:
        return ArtifactValidation(
            "product artifact Scene must use artifacts/model.scene.zip",
            "product_artifact",
        )
    if not facts.scene_parse.valid:
        return ArtifactValidation(
            facts.scene_parse.error or "canonical Scene Artifact could not be parsed",
            "scene",
        )
    if review_evidence_error is not None:
        return ArtifactValidation(
            f"CAD review evidence could not be generated: {review_evidence_error}",
            "review_evidence",
        )
    if not review_manifest_path or not review_artifact_dir:
        return ArtifactValidation(
            "CAD review evidence was not generated", "review_evidence"
        )
    return ArtifactValidation()


def artifact_entries_for(artifact_dir: Path) -> tuple[str, ...]:
    if not artifact_dir.is_dir():
        return ()
    entries = []
    for path in artifact_dir.rglob("*"):
        relative = path.relative_to(artifact_dir)
        if relative.parts and _ARTIFACT_VERSION_DIRECTORY.fullmatch(relative.parts[0]):
            continue
        if path.is_file() or path.is_symlink():
            entries.append(relative.as_posix())
    return tuple(sorted(entries))


def declared_artifact_entries(artifact: ProductArtifact) -> tuple[str, ...]:
    entries = {PRODUCT_ARTIFACT_MANIFEST_NAME}
    entries.update(record.relative_path for record in artifact.files.values())
    entries.update(part.file.relative_path for part in artifact.parts)
    return tuple(sorted(entries))


def artifact_has_symlink(artifact_dir: Path) -> bool:
    if artifact_dir.is_symlink():
        return True
    for path in artifact_dir.rglob("*"):
        relative = path.relative_to(artifact_dir)
        if relative.parts and _ARTIFACT_VERSION_DIRECTORY.fullmatch(relative.parts[0]):
            continue
        if path.is_symlink():
            return True
    return False


def _volume_matches(expected: float, observed: float) -> bool:
    return math.isclose(expected, observed, rel_tol=1e-12, abs_tol=1e-12)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


_artifact_entries = artifact_entries_for
_declared_artifact_entries = declared_artifact_entries
_artifact_has_symlink = artifact_has_symlink


__all__ = [
    "ArtifactValidation",
    "HostArtifactFacts",
    "artifact_entries_for",
    "artifact_has_symlink",
    "clear_artifacts",
    "collect_artifact_facts",
    "declared_artifact_entries",
    "validate_host_artifacts",
    "validate_project_paths",
]
