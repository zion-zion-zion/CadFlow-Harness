"""Validated access to one semantic CAD product artifact bundle."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


PRODUCT_ARTIFACT_MANIFEST_NAME = "product.json"
PRODUCT_ARTIFACT_SCHEMA_VERSION = "cadflow-product/v1"
MAX_PRODUCT_MANIFEST_BYTES = 1024 * 1024
ACCEPTED_PRODUCT_FILE_ROLES = frozenset(
    {
        "assumptions",
        "bom",
        "product_step",
        "scene",
        "semantic_model",
        "source_snapshot",
        "validation_report",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProductArtifactError(ValueError):
    """Raised when a product bundle does not satisfy its manifest."""


class ProductArtifactStatus(str, Enum):
    """Automated disposition recorded for a product bundle."""

    DRAFT = "Draft"
    ACCEPTED = "Accepted"


@dataclass(frozen=True)
class ProductSummary:
    """Bounded geometric and product-structure facts from one execution."""

    component_count: int
    leaf_part_count: int
    unique_part_count: int
    solid_count: int
    volume_mm3: float


@dataclass(frozen=True)
class ProductFile:
    """One content-addressed file inside a product bundle."""

    role: str
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ProductPart:
    """One unique Part definition and all of its component instances."""

    part_id: str
    quantity: int
    component_paths: tuple[str, ...]
    file: ProductFile


@dataclass(frozen=True)
class ProductBomItem:
    """One verified BOM row corresponding to a unique Part definition."""

    part_id: str
    name: str | None
    material: Any
    quantity: int
    component_paths: tuple[str, ...]
    step_path: str


@dataclass(frozen=True)
class ProductArtifact:
    """A verified product bundle exposed through a small stable interface."""

    root: Path
    result_kind: str
    status: ProductArtifactStatus
    summary: ProductSummary
    files: Mapping[str, ProductFile]
    parts: tuple[ProductPart, ...]
    semantic_model: Mapping[str, Any] | None
    bom: tuple[ProductBomItem, ...]
    assumptions: tuple[str, ...]
    validation_report: Mapping[str, Any] | None

    def file_path(self, role: str) -> Path:
        """Return the already-verified path for one uniquely named file role."""

        try:
            record = self.files[role]
        except KeyError as exc:
            raise ProductArtifactError(f"product artifact has no {role!r} file") from exc
        return self.root.joinpath(*PurePosixPath(record.relative_path).parts)

    def part_file_path(self, part_id: str) -> Path:
        """Return the verified STEP path for one unique Part definition."""

        for part in self.parts:
            if part.part_id == part_id:
                return self.root.joinpath(*PurePosixPath(part.file.relative_path).parts)
        raise ProductArtifactError(f"product artifact has no Part {part_id!r}")

    def require_complete(self) -> None:
        """Require every file and count needed before automated acceptance."""

        missing_roles = sorted(ACCEPTED_PRODUCT_FILE_ROLES.difference(self.files))
        if missing_roles:
            raise ProductArtifactError(
                "Accepted product is missing required files: "
                + ", ".join(missing_roles)
            )
        if (
            self.semantic_model is None
            or not self.bom
            or self.validation_report is None
        ):
            raise ProductArtifactError(
                "complete product has missing structured artifact data"
            )
        _validate_accepted_product(self.result_kind, self.summary, self.parts)


def load_product_artifact(bundle_root: str | Path) -> ProductArtifact:
    """Load and verify the versioned manifest and every declared file."""

    candidate = Path(bundle_root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ProductArtifactError("product artifact root must be a real directory")
    root = candidate.resolve()
    manifest_path = root / PRODUCT_ARTIFACT_MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ProductArtifactError("product artifact manifest is missing")
    try:
        if manifest_path.stat().st_size > MAX_PRODUCT_MANIFEST_BYTES:
            raise ProductArtifactError("product artifact manifest is too large")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductArtifactError("product artifact manifest is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ProductArtifactError("product artifact manifest must be an object")
    if payload.get("schema_version") != PRODUCT_ARTIFACT_SCHEMA_VERSION:
        raise ProductArtifactError("unsupported product artifact schema version")

    result_kind = payload.get("result_kind")
    if result_kind not in {"part", "assembly"}:
        raise ProductArtifactError("product artifact result_kind is invalid")
    try:
        status = ProductArtifactStatus(payload.get("status"))
    except ValueError as exc:
        raise ProductArtifactError("product artifact status is invalid") from exc

    summary = _load_summary(payload.get("summary"))
    raw_files = payload.get("files")
    if not isinstance(raw_files, Mapping) or not raw_files:
        raise ProductArtifactError("product artifact files must be a non-empty object")
    files: dict[str, ProductFile] = {}
    observed_paths: set[str] = set()
    for role, raw_record in raw_files.items():
        if not isinstance(role, str) or not role:
            raise ProductArtifactError("product artifact file role is invalid")
        record = _load_file(root, role, raw_record)
        if record.relative_path in observed_paths:
            raise ProductArtifactError("product artifact file paths must be unique")
        observed_paths.add(record.relative_path)
        files[role] = record
    parts = _load_parts(root, payload.get("parts", ()), observed_paths)
    semantic_model = (
        _load_semantic_model(
            root.joinpath(*PurePosixPath(files["semantic_model"].relative_path).parts),
            result_kind=result_kind,
            summary=summary,
            parts=parts,
        )
        if "semantic_model" in files
        else None
    )
    bom = (
        _load_bom(
            root.joinpath(*PurePosixPath(files["bom"].relative_path).parts),
            parts=parts,
            semantic_model=semantic_model,
        )
        if "bom" in files
        else ()
    )
    assumptions = (
        _load_assumptions(
            root.joinpath(*PurePosixPath(files["assumptions"].relative_path).parts)
        )
        if "assumptions" in files
        else ()
    )
    validation_report = (
        _load_validation_report(
            root.joinpath(
                *PurePosixPath(files["validation_report"].relative_path).parts
            )
        )
        if "validation_report" in files
        else None
    )
    artifact = ProductArtifact(
        root=root,
        result_kind=str(result_kind),
        status=status,
        summary=summary,
        files=MappingProxyType(files),
        parts=parts,
        semantic_model=semantic_model,
        bom=bom,
        assumptions=assumptions,
        validation_report=validation_report,
    )
    if status is ProductArtifactStatus.ACCEPTED:
        artifact.require_complete()
        _require_accepted_validation(artifact)
    return artifact


def accept_product_artifact(
    bundle_root: str | Path,
    *,
    scene_evidence: Mapping[str, Any],
    review_evidence: Mapping[str, Any],
) -> ProductArtifact:
    """Promote one deterministically passed Draft after the final two gates."""

    artifact = load_product_artifact(bundle_root)
    artifact.require_complete()
    if artifact.status is not ProductArtifactStatus.DRAFT:
        raise ProductArtifactError("only a Draft product artifact can be accepted")
    if not isinstance(scene_evidence, Mapping) or scene_evidence.get("valid") is not True:
        raise ProductArtifactError("Accepted product requires a valid parsed Scene")
    if not isinstance(review_evidence, Mapping) or review_evidence.get("status") != "pass":
        raise ProductArtifactError("Accepted product requires a passing independent review")

    validation_path = artifact.file_path("validation_report")
    validation = _read_json_object(validation_path, "product validation report")
    if validation.get("schema_version") != "cadflow-validation/v1":
        raise ProductArtifactError("product validation report schema is invalid")
    if validation.get("status") != "Passed":
        raise ProductArtifactError(
            "Accepted product requires Passed deterministic validation"
        )
    failures = validation.get("blocking_failures")
    if not isinstance(failures, list) or failures:
        raise ProductArtifactError(
            "Accepted product deterministic validation has blocking failures"
        )
    checks = validation.get("checks")
    if not isinstance(checks, list):
        raise ProductArtifactError("product validation checks must be a list")
    check_statuses: dict[str, str] = {}
    for check in checks:
        if not isinstance(check, Mapping):
            raise ProductArtifactError("product validation check is invalid")
        check_id = check.get("check_id")
        status = check.get("status")
        if (
            not isinstance(check_id, str)
            or not check_id
            or check_id in check_statuses
            or status not in {"passed", "failed", "not_applicable"}
        ):
            raise ProductArtifactError("product validation check identity is invalid")
        check_statuses[check_id] = str(status)
    required_passed = {"leaf_geometry", "product_spec", "step_export_replay"}
    if artifact.result_kind == "assembly":
        required_passed.update(
            {
                "constraint_residuals",
                "current_pose_collision",
                "envelope",
                "envelope_spec",
                "strict_constraint_solve",
            }
        )
    if any(check_statuses.get(check_id) != "passed" for check_id in required_passed):
        raise ProductArtifactError(
            "Accepted product is missing a passed deterministic check"
        )
    if artifact.result_kind == "part" and check_statuses.get("envelope") not in {
        "passed",
        "not_applicable",
    }:
        raise ProductArtifactError("Accepted Part has an invalid envelope check")
    if "scene_parse" in check_statuses or "independent_review" in check_statuses:
        raise ProductArtifactError("final acceptance checks are already present")

    accepted_validation = dict(validation)
    accepted_validation["status"] = "Accepted"
    accepted_validation["checks"] = [
        *checks,
        {
            "check_id": "scene_parse",
            "status": "passed",
            "evidence": dict(scene_evidence),
        },
        {
            "check_id": "independent_review",
            "status": "passed",
            "evidence": dict(review_evidence),
        },
    ]
    _write_json_atomic(validation_path, accepted_validation)

    manifest_path = artifact.root / PRODUCT_ARTIFACT_MANIFEST_NAME
    manifest = _read_json_object(manifest_path, "product artifact manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or not isinstance(
        files.get("validation_report"), dict
    ):
        raise ProductArtifactError("product validation file record is missing")
    files["validation_report"] = _file_record_for_manifest(
        artifact.root,
        validation_path,
    )
    manifest["status"] = ProductArtifactStatus.ACCEPTED.value
    _write_json_atomic(manifest_path, manifest)
    return load_product_artifact(artifact.root)


def _load_summary(value: Any) -> ProductSummary:
    if not isinstance(value, Mapping):
        raise ProductArtifactError("product artifact summary must be an object")
    counts = {
        name: _non_negative_int(value.get(name), name)
        for name in (
            "component_count",
            "leaf_part_count",
            "unique_part_count",
            "solid_count",
        )
    }
    volume = value.get("volume_mm3")
    if (
        not isinstance(volume, (int, float))
        or isinstance(volume, bool)
        or not math.isfinite(volume)
        or volume <= 0.0
    ):
        raise ProductArtifactError("product artifact volume_mm3 must be positive")
    return ProductSummary(volume_mm3=float(volume), **counts)


def _non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProductArtifactError(f"product artifact {name} must be non-negative")
    return value


def _load_file(root: Path, role: str, value: Any) -> ProductFile:
    if not isinstance(value, Mapping):
        raise ProductArtifactError(f"product artifact {role!r} file must be an object")
    relative_path = value.get("path")
    if not isinstance(relative_path, str):
        raise ProductArtifactError(f"product artifact {role!r} path is invalid")
    pure_path = PurePosixPath(relative_path)
    if (
        not relative_path
        or "\\" in relative_path
        or pure_path.is_absolute()
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise ProductArtifactError(f"product artifact {role!r} path is unsafe")
    sha256 = value.get("sha256")
    if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ProductArtifactError(f"product artifact {role!r} sha256 is invalid")
    size_bytes = value.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        raise ProductArtifactError(f"product artifact {role!r} size_bytes is invalid")

    path = root.joinpath(*pure_path.parts)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ProductArtifactError(f"product artifact {role!r} file is missing or unsafe") from exc
    if path.is_symlink() or not path.is_file():
        raise ProductArtifactError(f"product artifact {role!r} file is missing or unsafe")
    if path.stat().st_size != size_bytes:
        raise ProductArtifactError(f"product artifact {role!r} size does not match")
    if _sha256(path) != sha256:
        raise ProductArtifactError(f"product artifact {role!r} sha256 does not match")
    return ProductFile(
        role=role,
        relative_path=relative_path,
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _load_parts(
    root: Path,
    value: Any,
    observed_paths: set[str],
) -> tuple[ProductPart, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProductArtifactError("product artifact parts must be a list")
    parts: list[ProductPart] = []
    part_ids: set[str] = set()
    observed_component_paths: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ProductArtifactError("product artifact Part record must be an object")
        part_id = item.get("part_id")
        if not isinstance(part_id, str) or not part_id.strip():
            raise ProductArtifactError("product artifact Part ID is invalid")
        part_id = part_id.strip()
        if part_id in part_ids:
            raise ProductArtifactError("product artifact Part IDs must be unique")
        part_ids.add(part_id)
        quantity = item.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise ProductArtifactError("product artifact Part quantity must be positive")
        raw_component_paths = item.get("component_paths")
        if not isinstance(raw_component_paths, (list, tuple)):
            raise ProductArtifactError("product artifact component_paths must be a list")
        component_paths: list[str] = []
        for component_path in raw_component_paths:
            if not isinstance(component_path, str) or not component_path.strip():
                raise ProductArtifactError("product artifact component path is invalid")
            component_path = component_path.strip()
            _validate_component_path(component_path)
            if component_path in observed_component_paths:
                raise ProductArtifactError(
                    "product artifact component paths must be globally unique"
                )
            observed_component_paths.add(component_path)
            component_paths.append(component_path)
        if len(component_paths) != quantity:
            raise ProductArtifactError(
                "product artifact Part quantity must match its component paths"
            )
        file = _load_file(root, f"part_step[{index}]", item.get("file"))
        if file.relative_path in observed_paths:
            raise ProductArtifactError("product artifact file paths must be unique")
        observed_paths.add(file.relative_path)
        parts.append(
            ProductPart(
                part_id=part_id,
                quantity=quantity,
                component_paths=tuple(component_paths),
                file=file,
            )
        )
    return tuple(parts)


def _validate_component_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ProductArtifactError("product artifact component path is unsafe")


def _load_semantic_model(
    path: Path,
    *,
    result_kind: str,
    summary: ProductSummary,
    parts: tuple[ProductPart, ...],
) -> Mapping[str, Any]:
    value = _read_json_object(path, "product semantic model")
    if value.get("schema_version") != "cadflow-semantic-model/v1":
        raise ProductArtifactError("product semantic model schema is invalid")
    if value.get("result_kind") != result_kind:
        raise ProductArtifactError("product semantic model result kind is invalid")
    root = value.get("root")
    if not isinstance(root, Mapping):
        raise ProductArtifactError("product semantic model root is invalid")
    root_kind = root.get("item_kind")
    root_id = root.get("item_id")
    if root_kind != result_kind or not isinstance(root_id, str) or not root_id:
        raise ProductArtifactError("product semantic model root is invalid")

    raw_part_definitions = value.get("part_definitions")
    if not isinstance(raw_part_definitions, list):
        raise ProductArtifactError("product semantic Part definitions must be a list")
    manifest_parts = {part.part_id: part for part in parts}
    semantic_parts: dict[str, Mapping[str, Any]] = {}
    total_volume = 0.0
    for definition in raw_part_definitions:
        if not isinstance(definition, Mapping):
            raise ProductArtifactError("product semantic Part definition is invalid")
        part_id = definition.get("part_id")
        if (
            not isinstance(part_id, str)
            or not part_id
            or part_id in semantic_parts
            or part_id not in manifest_parts
        ):
            raise ProductArtifactError("product semantic Part identity is invalid")
        if not isinstance(definition.get("connectors"), list):
            raise ProductArtifactError("product semantic Part connectors are invalid")
        name = definition.get("name")
        if name is not None and not isinstance(name, str):
            raise ProductArtifactError("product semantic Part name is invalid")
        body = definition.get("body")
        if not isinstance(body, Mapping):
            raise ProductArtifactError("product semantic Part body is invalid")
        manifest_part = manifest_parts[part_id]
        if (
            body.get("step_path") != manifest_part.file.relative_path
            or body.get("step_sha256") != manifest_part.file.sha256
        ):
            raise ProductArtifactError(
                "product semantic Part STEP does not match the manifest"
            )
        volume = body.get("volume_mm3")
        if (
            not isinstance(volume, (int, float))
            or isinstance(volume, bool)
            or not math.isfinite(volume)
            or volume <= 0.0
        ):
            raise ProductArtifactError("product semantic Part volume is invalid")
        total_volume += float(volume) * manifest_part.quantity
        semantic_parts[part_id] = definition
    if set(semantic_parts) != set(manifest_parts):
        raise ProductArtifactError(
            "product semantic Part definitions do not match the manifest"
        )
    if not math.isclose(
        total_volume,
        summary.volume_mm3,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ProductArtifactError(
            "product semantic Part volumes do not match the product summary"
        )

    raw_assemblies = value.get("assembly_definitions")
    if not isinstance(raw_assemblies, list):
        raise ProductArtifactError(
            "product semantic Assembly definitions must be a list"
        )
    assemblies: dict[str, Mapping[str, Any]] = {}
    for definition in raw_assemblies:
        if not isinstance(definition, Mapping):
            raise ProductArtifactError(
                "product semantic Assembly definition is invalid"
            )
        assembly_id = definition.get("assembly_id")
        if (
            not isinstance(assembly_id, str)
            or not assembly_id
            or assembly_id in assemblies
            or not isinstance(definition.get("components"), list)
        ):
            raise ProductArtifactError(
                "product semantic Assembly identity is invalid"
            )
        assemblies[assembly_id] = definition

    expected_paths = {
        part.part_id: tuple(part.component_paths) for part in parts
    }
    if result_kind == "part":
        if assemblies or root_id != "model" or expected_paths != {"model": ("model",)}:
            raise ProductArtifactError("product semantic Part root is inconsistent")
        component_count = 0
        leaf_paths = {"model": ("model",)}
    else:
        if root_id not in assemblies:
            raise ProductArtifactError(
                "product semantic Assembly root definition is missing"
            )
        component_count, mutable_paths = _walk_semantic_assembly(
            root_id,
            assemblies=assemblies,
            part_ids=set(semantic_parts),
            ancestors=(),
            path=(root_id,),
        )
        leaf_paths = {
            part_id: tuple(paths) for part_id, paths in mutable_paths.items()
        }
    if component_count != summary.component_count:
        raise ProductArtifactError(
            "product semantic component count does not match the summary"
        )
    if sum(len(paths) for paths in leaf_paths.values()) != summary.leaf_part_count:
        raise ProductArtifactError(
            "product semantic leaf-Part count does not match the summary"
        )
    if leaf_paths != expected_paths:
        raise ProductArtifactError(
            "product semantic component paths do not match the manifest"
        )
    return MappingProxyType(dict(value))


def _walk_semantic_assembly(
    assembly_id: str,
    *,
    assemblies: Mapping[str, Mapping[str, Any]],
    part_ids: set[str],
    ancestors: tuple[str, ...],
    path: tuple[str, ...],
) -> tuple[int, dict[str, list[str]]]:
    if assembly_id in ancestors:
        raise ProductArtifactError("product semantic Assembly graph is cyclic")
    definition = assemblies[assembly_id]
    components = definition["components"]
    assert isinstance(components, list)
    if not components:
        raise ProductArtifactError(
            "product semantic Assembly definitions must contain components"
        )
    component_ids: set[str] = set()
    component_count = 0
    leaf_paths: dict[str, list[str]] = {}
    for component in components:
        if not isinstance(component, Mapping):
            raise ProductArtifactError("product semantic component is invalid")
        component_id = component.get("component_id")
        item_kind = component.get("item_kind")
        item_id = component.get("item_id")
        if (
            not isinstance(component_id, str)
            or not component_id
            or component_id in component_ids
            or not isinstance(item_id, str)
            or not item_id
        ):
            raise ProductArtifactError("product semantic component identity is invalid")
        component_ids.add(component_id)
        _validate_semantic_placement(component.get("placement"))
        component_count += 1
        component_path = path + (component_id,)
        if item_kind == "part" and item_id in part_ids:
            leaf_paths.setdefault(item_id, []).append("/".join(component_path))
        elif item_kind == "assembly" and item_id in assemblies:
            nested_count, nested_paths = _walk_semantic_assembly(
                item_id,
                assemblies=assemblies,
                part_ids=part_ids,
                ancestors=ancestors + (assembly_id,),
                path=component_path,
            )
            component_count += nested_count
            for part_id, paths in nested_paths.items():
                leaf_paths.setdefault(part_id, []).extend(paths)
        else:
            raise ProductArtifactError("product semantic component target is invalid")
    return component_count, leaf_paths


def _validate_semantic_placement(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ProductArtifactError("product semantic component placement is invalid")
    for name in ("origin", "x_axis", "y_axis", "z_axis"):
        coordinates = value.get(name)
        if (
            not isinstance(coordinates, (list, tuple))
            or len(coordinates) != 3
            or any(
                not isinstance(coordinate, (int, float))
                or isinstance(coordinate, bool)
                or not math.isfinite(coordinate)
                for coordinate in coordinates
            )
        ):
            raise ProductArtifactError(
                "product semantic component placement is invalid"
            )


def _load_bom(
    path: Path,
    *,
    parts: tuple[ProductPart, ...],
    semantic_model: Mapping[str, Any] | None,
) -> tuple[ProductBomItem, ...]:
    value = _read_json_object(path, "product BOM")
    if value.get("schema_version") != "cadflow-bom/v1":
        raise ProductArtifactError("product BOM schema is invalid")
    raw_items = value.get("items")
    if not isinstance(raw_items, list):
        raise ProductArtifactError("product BOM items must be a list")
    manifest_parts = {part.part_id: part for part in parts}
    semantic_parts: dict[str, Mapping[str, Any]] = {}
    if semantic_model is not None:
        raw_semantic_parts = semantic_model.get("part_definitions")
        if isinstance(raw_semantic_parts, list):
            semantic_parts = {
                str(item["part_id"]): item
                for item in raw_semantic_parts
                if isinstance(item, Mapping) and isinstance(item.get("part_id"), str)
            }
    items: list[ProductBomItem] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ProductArtifactError("product BOM item is invalid")
        part_id = raw_item.get("part_id")
        if (
            not isinstance(part_id, str)
            or part_id in seen
            or part_id not in manifest_parts
        ):
            raise ProductArtifactError("product BOM Part identity is invalid")
        seen.add(part_id)
        manifest_part = manifest_parts[part_id]
        quantity = raw_item.get("quantity")
        if quantity != manifest_part.quantity:
            raise ProductArtifactError("product BOM quantity does not match the manifest")
        raw_paths = raw_item.get("component_paths")
        if not isinstance(raw_paths, list) or tuple(raw_paths) != manifest_part.component_paths:
            raise ProductArtifactError(
                "product BOM component paths do not match the manifest"
            )
        if raw_item.get("step_path") != manifest_part.file.relative_path:
            raise ProductArtifactError("product BOM STEP path does not match the manifest")
        name = raw_item.get("name")
        if name is not None and not isinstance(name, str):
            raise ProductArtifactError("product BOM name is invalid")
        semantic_part = semantic_parts.get(part_id)
        material = raw_item.get("material")
        if semantic_part is not None and (
            name != semantic_part.get("name")
            or material != semantic_part.get("material")
        ):
            raise ProductArtifactError(
                "product BOM metadata does not match the semantic Part"
            )
        items.append(
            ProductBomItem(
                part_id=part_id,
                name=name,
                material=material,
                quantity=int(quantity),
                component_paths=manifest_part.component_paths,
                step_path=manifest_part.file.relative_path,
            )
        )
    if seen != set(manifest_parts):
        raise ProductArtifactError("product BOM does not cover every unique Part")
    return tuple(items)


def _load_assumptions(path: Path) -> tuple[str, ...]:
    value = _read_json_object(path, "product assumptions")
    if value.get("schema_version") != "cadflow-assumptions/v1":
        raise ProductArtifactError("product assumptions schema is invalid")
    raw_assumptions = value.get("assumptions")
    if not isinstance(raw_assumptions, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_assumptions
    ):
        raise ProductArtifactError(
            "every product assumption must be a non-empty string"
        )
    return tuple(item.strip() for item in raw_assumptions)


def _load_validation_report(path: Path) -> Mapping[str, Any]:
    value = _read_json_object(path, "product validation report")
    if value.get("schema_version") != "cadflow-validation/v1":
        raise ProductArtifactError("product validation report schema is invalid")
    if value.get("status") not in {"Draft", "Passed", "Accepted"}:
        raise ProductArtifactError("product validation report status is invalid")
    failures = value.get("blocking_failures")
    if not isinstance(failures, list) or any(
        not isinstance(item, str) or not item for item in failures
    ):
        raise ProductArtifactError(
            "product validation blocking failures are invalid"
        )
    checks = value.get("checks")
    if not isinstance(checks, list):
        raise ProductArtifactError("product validation checks must be a list")
    check_ids: set[str] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            raise ProductArtifactError("product validation check is invalid")
        check_id = check.get("check_id")
        if (
            not isinstance(check_id, str)
            or not check_id
            or check_id in check_ids
            or check.get("status") not in {"passed", "failed", "not_applicable"}
        ):
            raise ProductArtifactError("product validation check identity is invalid")
        check_ids.add(check_id)
    if value["status"] in {"Passed", "Accepted"} and failures:
        raise ProductArtifactError(
            "passed product validation cannot contain blocking failures"
        )
    return MappingProxyType(dict(value))


def _validate_accepted_product(
    result_kind: Any,
    summary: ProductSummary,
    parts: tuple[ProductPart, ...],
) -> None:
    if not parts:
        raise ProductArtifactError("Accepted product is missing unique-Part STEP files")
    if len(parts) != summary.unique_part_count:
        raise ProductArtifactError(
            "Accepted product unique-Part count does not match its manifest"
        )
    if sum(part.quantity for part in parts) != summary.leaf_part_count:
        raise ProductArtifactError(
            "Accepted product Part quantities do not match its leaf-Part count"
        )
    if summary.solid_count != summary.leaf_part_count:
        raise ProductArtifactError(
            "Accepted product must contain one solid for every leaf Part"
        )
    if result_kind == "assembly":
        if summary.leaf_part_count < 1 or summary.component_count < summary.leaf_part_count:
            raise ProductArtifactError("Accepted Assembly structure counts are invalid")
    elif (
        summary.component_count != 0
        or summary.leaf_part_count != 1
        or summary.unique_part_count != 1
    ):
        raise ProductArtifactError("Accepted Part structure counts are invalid")


def _require_accepted_validation(artifact: ProductArtifact) -> None:
    validation = _read_json_object(
        artifact.file_path("validation_report"),
        "Accepted validation report",
    )
    if (
        validation.get("schema_version") != "cadflow-validation/v1"
        or validation.get("status") != ProductArtifactStatus.ACCEPTED.value
        or validation.get("blocking_failures") != []
    ):
        raise ProductArtifactError("Accepted validation report status is invalid")
    checks = validation.get("checks")
    if not isinstance(checks, list):
        raise ProductArtifactError("Accepted validation report checks are invalid")
    statuses: dict[str, str] = {}
    for check in checks:
        if not isinstance(check, Mapping):
            raise ProductArtifactError("Accepted validation report check is invalid")
        check_id = check.get("check_id")
        status = check.get("status")
        if (
            not isinstance(check_id, str)
            or not check_id
            or check_id in statuses
            or status not in {"passed", "not_applicable"}
        ):
            raise ProductArtifactError("Accepted validation report check is invalid")
        statuses[check_id] = str(status)
    required_passed = {
        "independent_review",
        "leaf_geometry",
        "product_spec",
        "scene_parse",
        "step_export_replay",
    }
    if artifact.result_kind == "assembly":
        required_passed.update(
            {
                "constraint_residuals",
                "current_pose_collision",
                "envelope",
                "envelope_spec",
                "strict_constraint_solve",
            }
        )
    if any(statuses.get(check_id) != "passed" for check_id in required_passed):
        raise ProductArtifactError(
            "Accepted validation report is missing a passed acceptance check"
        )
    if artifact.result_kind == "part" and statuses.get("envelope") not in {
        "passed",
        "not_applicable",
    }:
        raise ProductArtifactError("Accepted validation report Part envelope is invalid")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductArtifactError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProductArtifactError(f"{label} must be an object")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    try:
        content = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ProductArtifactError("product acceptance evidence is not JSON-compatible") from exc
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _file_record_for_manifest(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


__all__ = [
    "ACCEPTED_PRODUCT_FILE_ROLES",
    "MAX_PRODUCT_MANIFEST_BYTES",
    "PRODUCT_ARTIFACT_MANIFEST_NAME",
    "PRODUCT_ARTIFACT_SCHEMA_VERSION",
    "ProductArtifact",
    "ProductArtifactError",
    "ProductArtifactStatus",
    "ProductFile",
    "ProductPart",
    "ProductSummary",
    "accept_product_artifact",
    "load_product_artifact",
]
