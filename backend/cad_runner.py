"""Internal subprocess entry point for trusted CadFlow Model Source execution."""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import os
import re
import runpy
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

import cadflow as cad


# Keep these three literals local because this file is also launched directly
# as ``python -u backend/cad_runner.py`` with the repository package path
# intentionally absent. ``backend.cad_protocol`` uses the same wire values for
# host-side decoding and tests lock the two definitions together.
PREFLIGHT_PREFIX = "__CADFLOW_PREFLIGHT__"
RESULT_PREFIX = "__CADFLOW_EXECUTION_RESULT__"
PHASE_PREFIX = "__CADFLOW_EXECUTION_PHASE__"
PRODUCT_MANIFEST_NAME = "product.json"
PRODUCT_SCHEMA_VERSION = "cadflow-product/v1"
SCENE_LINEAR_TOLERANCE_MM = 0.5
SCENE_ANGULAR_TOLERANCE_RADIANS = 0.2
DEFAULT_REVIEW_COMPONENT_COLOR = (0.55, 0.64, 0.73)
_ASSEMBLY_EARLY_GATE_CHECK_IDS = frozenset(
    {
        "strict_constraint_solve",
        "constraint_residuals",
    }
)


def _report_phase(phase: str) -> None:
    print(
        PHASE_PREFIX + json.dumps({"phase": phase}, separators=(",", ":")),
        flush=True,
    )


def _source_manifest(
    code_root: Path,
) -> tuple[str, list[dict[str, str]], list[tuple[str, bytes]]]:
    digest = hashlib.sha256()
    records: list[dict[str, str]] = []
    sources: list[tuple[str, bytes]] = []
    for path in sorted(code_root.rglob("*.py")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_text = path.relative_to(code_root).as_posix()
        relative = relative_text.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        records.append(
            {
                "path": relative_text,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        sources.append((relative_text, content))
    return digest.hexdigest(), records, sources


def _placement_coordinates(component: Any) -> list[Any]:
    placement = component.placement.to_dict()
    return [
        value
        for name in ("origin", "x_axis", "y_axis", "z_axis")
        for value in placement.get(name, ())
    ]


def _assembly_inventory(root: Any) -> dict[str, Any]:
    component_count = 0
    solid_count = 0
    solid_volume = 0.0
    assembly_definitions: dict[str, Any] = {}
    assembly_identities: dict[str, int] = {}
    part_definitions: dict[str, Any] = {}
    part_identities: dict[str, int] = {}
    occurrences: dict[str, list[str]] = {}

    def visit(assembly: Any, ancestors: set[int], path: tuple[str, ...]) -> None:
        nonlocal component_count, solid_count, solid_volume
        identity = id(assembly)
        if identity in ancestors:
            raise ValueError("Assembly component graph must not contain cycles")
        if not assembly.components:
            raise ValueError("Assembly and nested subassemblies must contain components")
        existing_identity = assembly_identities.get(assembly.assembly_id)
        if existing_identity is not None and existing_identity != identity:
            raise ValueError("Assembly IDs must identify one reusable definition")
        assembly_identities[assembly.assembly_id] = identity
        assembly_definitions.setdefault(assembly.assembly_id, assembly)
        next_ancestors = ancestors | {identity}
        component_ids: set[str] = set()
        for component in assembly.components:
            if component.component_id in component_ids:
                raise ValueError(
                    "Assembly component IDs must be unique within their parent"
                )
            component_ids.add(component.component_id)
            coordinates = _placement_coordinates(component)
            try:
                placement_is_finite = len(coordinates) == 12 and all(
                    math.isfinite(float(value)) for value in coordinates
                )
            except (TypeError, ValueError):
                placement_is_finite = False
            if not placement_is_finite:
                raise ValueError("Assembly component placements must be finite")
            component_count += 1
            component_path = path + (component.component_id,)
            if isinstance(component.item, cad.Part):
                part = component.item
                if not isinstance(part.body, cad.Solid):
                    raise TypeError("every Assembly leaf Part must contain one cad.Solid")
                volume = float(part.body.get_volume())
                if not math.isfinite(volume) or volume <= 0.0:
                    raise ValueError("every Assembly leaf Part must have positive volume")
                existing_part_identity = part_identities.get(part.part_id)
                if existing_part_identity is not None and existing_part_identity != id(part):
                    raise ValueError("Part IDs must identify one reusable definition")
                part_identities[part.part_id] = id(part)
                part_definitions.setdefault(part.part_id, part)
                occurrences.setdefault(part.part_id, []).append("/".join(component_path))
                solid_count += 1
                solid_volume += volume
            elif isinstance(component.item, cad.Assembly):
                visit(component.item, next_ancestors, component_path)
            else:
                raise TypeError("Assembly components must contain cad.Part or cad.Assembly")

    visit(root, set(), (root.assembly_id,))
    return {
        "component_count": component_count,
        "leaf_part_count": solid_count,
        "solid_count": solid_count,
        "solid_volume": solid_volume,
        "assembly_definitions": assembly_definitions,
        "part_definitions": part_definitions,
        "occurrences": occurrences,
    }


def _assembly_leaf_appearances(root: Any) -> list[dict[str, Any]]:
    appearances: list[dict[str, Any]] = []

    def visit(assembly: Any, path: tuple[str, ...]) -> None:
        for component in assembly.components:
            component_path = path + (component.component_id,)
            if isinstance(component.item, cad.Part):
                material = component.item.material
                appearances.append(
                    {
                        "component_path": "/".join(component_path),
                        "scene_node_id": "instance/main"
                        + "".join(
                            f"/{quote(item, safe='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')}"
                            for item in component_path[1:]
                        ),
                        "part_id": component.item.part_id,
                        "material_id": (
                            material.material_id if material is not None else None
                        ),
                        "base_color": (
                            [float(value) for value in material.color]
                            if material is not None and material.color is not None
                            else None
                        ),
                    }
                )
            else:
                visit(component.item, component_path)

    visit(root, (root.assembly_id,))
    return appearances


def _review_appearances(
    *,
    final_result: Any,
    result_kind: str,
    presentation: Mapping[str, Any] | None,
) -> list[dict[str, Any]] | None:
    if result_kind == "assembly":
        appearances = _assembly_leaf_appearances(final_result)
    elif presentation is not None:
        appearances = [
            {
                "component_path": "model",
                "scene_node_id": "instance/main",
                "part_id": "model",
                "material_id": None,
                "base_color": None,
            }
        ]
    else:
        return None

    if presentation is None:
        return appearances

    named = {
        item.get("name"): item
        for item in presentation.get("appearances", ())
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    by_node = {
        item["scene_node_id"]: item
        for item in appearances
        if isinstance(item.get("scene_node_id"), str)
    }
    for override in presentation.get("node_overrides", ()):
        if not isinstance(override, Mapping):
            continue
        target = by_node.get(override.get("node_id"))
        authored = named.get(override.get("appearance_name"))
        if target is None or authored is None:
            continue
        target.update(
            {
                "base_color": [float(value) for value in authored["base_color"][:3]],
                "presentation_appearance": {
                    key: authored[key]
                    for key in (
                        "name",
                        "base_color",
                        "metallic",
                        "roughness",
                        "alpha_mode",
                        "double_sided",
                        "edge_color",
                    )
                },
            }
        )
    return appearances


def _safe_part_filename(part_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", part_id).strip("-._") or "part"
    suffix = hashlib.sha256(part_id.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:48]}-{suffix}.step"


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _file_record(artifact_dir: Path, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(artifact_dir).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _write_source_snapshot(
    path: Path,
    sources: list[tuple[str, bytes]],
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in sources:
            info = zipfile.ZipInfo(
                filename=f"code/{relative_path}",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)


def _product_spec(namespace: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    value = namespace.get("PRODUCT_SPEC", {})
    if not isinstance(value, Mapping):
        return (
            {"assumptions": []},
            ["PRODUCT_SPEC must be a mapping when provided"],
        )
    failures: list[str] = []
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            failures.append("PRODUCT_SPEC keys must be strings")
            continue
        try:
            json.dumps(item, ensure_ascii=True)
        except (TypeError, ValueError):
            failures.append(
                f"PRODUCT_SPEC.{key} must contain JSON-compatible values"
            )
            continue
        normalized[key] = item
    assumptions = normalized.get("assumptions", ())
    if not isinstance(assumptions, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in assumptions
    ):
        failures.append("PRODUCT_SPEC assumptions must be non-empty strings")
        normalized["assumptions"] = []
    else:
        normalized["assumptions"] = [item.strip() for item in assumptions]
    return normalized, list(dict.fromkeys(failures))


def _scene_presentation(namespace: Mapping[str, Any]) -> dict[str, Any] | None:
    value = namespace.get("PRESENTATION")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("PRESENTATION must be a mapping when provided")
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise TypeError("PRESENTATION must contain JSON-compatible values") from error
    return json.loads(encoded)


def _base_validation_report(
    *,
    result_kind: str,
    inventory: Mapping[str, Any],
    spec: Mapping[str, Any],
    spec_failures: list[str],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {
            "check_id": "product_spec",
            "status": "failed" if spec_failures else "passed",
            **(
                {"message": "; ".join(spec_failures)}
                if spec_failures
                else {"evidence": {"assumption_count": len(spec["assumptions"])}}
            ),
        },
        {
            "check_id": "leaf_geometry",
            "status": "passed",
            "evidence": {
                "leaf_part_count": inventory["leaf_part_count"],
                "solid_count": inventory["solid_count"],
                "volume_mm3": inventory["solid_volume"],
            },
        }
    ]
    blocking_failures: list[str] = list(spec_failures)
    if result_kind == "assembly":
        envelope = spec.get("envelope")
        if not isinstance(envelope, Mapping):
            message = (
                "Assembly validation requires "
                "PRODUCT_SPEC.envelope.max_size_mm"
            )
            checks.append(
                {"check_id": "envelope_spec", "status": "failed", "message": message}
            )
            blocking_failures.append(message)
        else:
            max_size = envelope.get("max_size_mm")
            if (
                not isinstance(max_size, (list, tuple))
                or len(max_size) != 3
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or value <= 0.0
                    for value in max_size
                )
            ):
                message = (
                    "PRODUCT_SPEC.envelope.max_size_mm must contain three "
                    "positive finite dimensions"
                )
                checks.append(
                    {
                        "check_id": "envelope_spec",
                        "status": "failed",
                        "message": message,
                    }
                )
                blocking_failures.append(message)
            else:
                checks.append(
                    {
                        "check_id": "envelope_spec",
                        "status": "passed",
                        "evidence": {"max_size_mm": [float(value) for value in max_size]},
                    }
                )
    return {
        "schema_version": "cadflow-validation/v1",
        "status": "Draft" if blocking_failures else "Passed",
        "checks": checks,
        "blocking_failures": blocking_failures,
    }


def _record_validation_failure(report: dict[str, Any], message: str) -> None:
    failures = report["blocking_failures"]
    if message not in failures:
        failures.append(message)
    report["status"] = "Draft"


def _assembly_early_gate_failed(report: Mapping[str, Any]) -> bool:
    return any(
        isinstance(check, Mapping)
        and check.get("check_id") in _ASSEMBLY_EARLY_GATE_CHECK_IDS
        and check.get("status") == "failed"
        for check in report.get("checks", ())
    )


def _short_circuit_validation_checks(
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bound failed early-gate evidence before writing it to stdout."""

    result: list[dict[str, Any]] = []
    sequence_limits = {
        "failures": 12,
        "warnings": 8,
        "residuals": 24,
        "grounded_component_ids": 64,
        "solved_component_ids": 64,
        "unsolved_component_ids": 64,
    }
    for source in checks:
        if source.get("status") != "failed":
            continue
        check = json.loads(json.dumps(source, ensure_ascii=True))
        truncated = False
        message = check.get("message")
        if isinstance(message, str) and len(message) > 2048:
            check["message"] = message[:2048]
            truncated = True
        evidence = check.get("evidence")
        if isinstance(evidence, dict):
            for key, limit in sequence_limits.items():
                value = evidence.get(key)
                if isinstance(value, list) and len(value) > limit:
                    evidence[key] = value[:limit]
                    truncated = True
        if truncated:
            check["evidence_truncated"] = True
        result.append(check)
    return result


def _validate_assembly(
    *,
    assembly: Any,
    validation_report: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    checks = validation_report["checks"]
    solved_assembly = assembly
    constraint_report: Any | None = None
    strict_passed = False
    residual_source = "strict_solve"
    _report_phase("strict_constraint_solve")
    try:
        solved_assembly = cad.solve_assembly_constraints_rassembly(
            assembly=assembly,
            strict=True,
        )
        constraint_report = cad.inspect_assembly_constraints_rconstraintreport(
            assembly=solved_assembly
        )
        strict_passed = bool(
            constraint_report.solved
            and not constraint_report.unsolved_component_ids
        )
        checks.append(
            {
                "check_id": "strict_constraint_solve",
                "status": "passed" if strict_passed else "failed",
                "evidence": {
                    "strict": True,
                    "solved": bool(constraint_report.solved),
                    "grounded_component_ids": list(
                        constraint_report.grounded_component_ids
                    ),
                    "solved_component_ids": list(constraint_report.solved_component_ids),
                    "unsolved_component_ids": list(
                        constraint_report.unsolved_component_ids
                    ),
                },
            }
        )
        if not strict_passed:
            _record_validation_failure(
                validation_report,
                "strict Assembly constraint solve did not solve every component",
            )
    except Exception as error:
        diagnostic_error: str | None = None
        diagnostic_evidence: dict[str, Any] = {"strict": True}
        try:
            diagnostic_assembly = cad.solve_assembly_constraints_rassembly(
                assembly=assembly,
                strict=False,
            )
            constraint_report = cad.inspect_assembly_constraints_rconstraintreport(
                assembly=diagnostic_assembly
            )
            residual_source = "non_strict_diagnostic"
            diagnostic_evidence.update(
                {
                    "diagnostic_solved": bool(constraint_report.solved),
                    "grounded_component_ids": list(
                        constraint_report.grounded_component_ids
                    ),
                    "solved_component_ids": list(
                        constraint_report.solved_component_ids
                    ),
                    "unsolved_component_ids": list(
                        constraint_report.unsolved_component_ids
                    ),
                }
            )
        except Exception as diagnostic_exception:
            diagnostic_error = (
                type(diagnostic_exception).__name__
                + ": "
                + str(diagnostic_exception)
            )
            diagnostic_evidence["diagnostic_error"] = diagnostic_error
        checks.append(
            {
                "check_id": "strict_constraint_solve",
                "status": "failed",
                "message": type(error).__name__ + ": " + str(error),
                "evidence": diagnostic_evidence,
            }
        )
        _record_validation_failure(
            validation_report,
            "strict Assembly constraint solve failed",
        )

    if constraint_report is None:
        checks.append(
            {
                "check_id": "constraint_residuals",
                "status": "failed",
                "message": "constraint residuals are unavailable because strict solve failed",
            }
        )
        _record_validation_failure(
            validation_report,
            "Assembly constraint residuals could not be verified",
        )
    else:
        residuals = [
            {
                "constraint_id": residual.constraint_id,
                "translation_error": float(residual.translation_error),
                "angular_error_degrees": float(residual.angular_error_degrees),
                "within_tolerance": bool(residual.within_tolerance),
            }
            for residual in constraint_report.residuals
        ]
        residual_values_passed = all(
            item["within_tolerance"]
            and math.isfinite(item["translation_error"])
            and math.isfinite(item["angular_error_degrees"])
            for item in residuals
        )
        residuals_passed = strict_passed and residual_values_passed
        checks.append(
            {
                "check_id": "constraint_residuals",
                "status": "passed" if residuals_passed else "failed",
                **(
                    {
                        "message": (
                            "residuals are diagnostic because strict solve failed"
                        )
                    }
                    if not strict_passed
                    else {}
                ),
                "evidence": {
                    "source": residual_source,
                    "residuals": residuals,
                    "max_translation_error": max(
                        (item["translation_error"] for item in residuals),
                        default=0.0,
                    ),
                    "max_angular_error_degrees": max(
                        (item["angular_error_degrees"] for item in residuals),
                        default=0.0,
                    ),
                },
            }
        )
        if not strict_passed:
            _record_validation_failure(
                validation_report,
                "Assembly constraint residuals could not be verified",
            )
        elif not residual_values_passed:
            _record_validation_failure(
                validation_report,
                "one or more Assembly constraint residuals exceed SDK tolerance",
            )

    return solved_assembly, _assembly_inventory(solved_assembly)


def _complete_export_validation(
    *,
    artifact_dir: Path,
    result_kind: str,
    inventory: Mapping[str, Any],
    spec: Mapping[str, Any],
    product_step_path: Path,
    part_manifest_records: list[dict[str, Any]],
    validation_report: dict[str, Any],
) -> None:
    _report_phase("step_export_replay")
    checks = validation_report["checks"]
    product_inspection: Any | None = None
    part_inspections: list[Any] = []
    try:
        from cadflow.inspect import brep

        product_inspection = brep.inspect_step_rbrepinspection(product_step_path)
        for item in part_manifest_records:
            part_path = artifact_dir.joinpath(*item["file"]["path"].split("/"))
            part_inspections.append(brep.inspect_step_rbrepinspection(part_path))
        expected_solids = inventory["leaf_part_count"]
        product_solid_count = int(product_inspection.counts.get("solid", 0))
        replay_passed = bool(
            product_inspection.valid
            and product_solid_count == expected_solids
            and len(part_inspections) == len(part_manifest_records)
            and all(
                inspection.valid
                and int(inspection.counts.get("solid", 0)) == 1
                and math.isfinite(float(inspection.volume))
                and float(inspection.volume) > 0.0
                for inspection in part_inspections
            )
        )
        checks.append(
            {
                "check_id": "step_export_replay",
                "status": "passed" if replay_passed else "failed",
                "evidence": {
                    "product_solid_count": product_solid_count,
                    "expected_product_solid_count": expected_solids,
                    "unique_part_step_count": len(part_inspections),
                    "all_part_steps_single_solid": all(
                        int(inspection.counts.get("solid", 0)) == 1
                        for inspection in part_inspections
                    ),
                },
            }
        )
        if not replay_passed:
            _record_validation_failure(
                validation_report,
                "STEP export replay did not preserve the product solid inventory",
            )
    except Exception as error:
        checks.append(
            {
                "check_id": "step_export_replay",
                "status": "failed",
                "message": type(error).__name__ + ": " + str(error),
            }
        )
        _record_validation_failure(
            validation_report,
            "STEP exports could not be replayed and inspected",
        )

    if result_kind == "part":
        checks.append({"check_id": "envelope", "status": "not_applicable"})
    else:
        envelope = spec.get("envelope")
        max_size = envelope.get("max_size_mm") if isinstance(envelope, Mapping) else None
        if product_inspection is None or not isinstance(max_size, (list, tuple)):
            checks.append(
                {
                    "check_id": "envelope",
                    "status": "failed",
                    "message": "product envelope could not be checked",
                }
            )
            _record_validation_failure(
                validation_report,
                "Assembly envelope could not be checked",
            )
        else:
            bounding_box = [float(value) for value in product_inspection.bounding_box]
            actual_size = [
                bounding_box[index + 3] - bounding_box[index] for index in range(3)
            ]
            maximum_size = [float(value) for value in max_size]
            envelope_passed = all(
                actual <= maximum + 1e-6
                for actual, maximum in zip(actual_size, maximum_size, strict=True)
            )
            checks.append(
                {
                    "check_id": "envelope",
                    "status": "passed" if envelope_passed else "failed",
                    "evidence": {
                        "bounding_box_mm": bounding_box,
                        "actual_size_mm": actual_size,
                        "max_size_mm": maximum_size,
                    },
                }
            )
            if not envelope_passed:
                _record_validation_failure(
                    validation_report,
                    "Assembly exceeds PRODUCT_SPEC.envelope.max_size_mm",
                )

    validation_report["status"] = (
        "Draft" if validation_report["blocking_failures"] else "Passed"
    )


def _export_product_bundle(
    *,
    artifact_dir: Path,
    final_result: Any,
    result_kind: str,
    inventory: Mapping[str, Any],
    spec: Mapping[str, Any],
    presentation: Mapping[str, Any] | None,
    validation_report: Mapping[str, Any],
    sources: list[tuple[str, bytes]],
) -> tuple[Path, dict[str, Any]]:
    _report_phase("product_step_export")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    product_step_path = artifact_dir / "model.step"
    scene_path = artifact_dir / "model.scene.zip"

    if result_kind == "part":
        final_result.export_step(str(product_step_path))
        ocp_shape = cad.inspection.brep.load_step_rshape(product_step_path)
        scene_root = cad.Solid(ocp_shape)
    else:
        preview = cad.make_compound_from_assembly_rcompound(assembly=final_result)
        cad.export_step(shapes=preview, filename=str(product_step_path))
        scene_root = final_result

    _report_phase("scene_export")
    package = cad.compile_scene(
        scene_id="model",
        roots=(cad.SceneRoot(root_id="main", value=scene_root),),
        source=cad.SceneSource(kind="manual", source_id="model.py"),
        options=cad.SceneCompileOptions(
            linear_tolerance=SCENE_LINEAR_TOLERANCE_MM,
            angular_tolerance=SCENE_ANGULAR_TOLERANCE_RADIANS,
        ),
    )
    if presentation is not None:
        package = cad.apply_presentation(
            package=package,
            presentation=presentation,
            embed_presentation=True,
        )
    cad.export_scene(package=package, path=scene_path)

    _report_phase("unique_part_step_export")
    parts_dir = artifact_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_manifest_records: list[dict[str, Any]] = []
    semantic_parts: list[dict[str, Any]] = []
    bom_items: list[dict[str, Any]] = []
    part_definitions = inventory["part_definitions"]
    occurrences = inventory["occurrences"]
    for part_id in sorted(part_definitions):
        part = part_definitions[part_id]
        part_step_path = parts_dir / _safe_part_filename(part_id)
        if result_kind == "part":
            shutil.copy2(product_step_path, part_step_path)
            part_payload = {
                "part_id": part_id,
                "name": "Model",
                "material": None,
                "connectors": [],
            }
            volume = float(final_result.volume)
        else:
            cad.export_step(shapes=part.body, filename=str(part_step_path))
            part_payload = part.to_dict()
            volume = float(part.body.get_volume())
        component_paths = tuple(occurrences[part_id])
        part_file = _file_record(artifact_dir, part_step_path)
        part_manifest_records.append(
            {
                "part_id": part_id,
                "quantity": len(component_paths),
                "component_paths": list(component_paths),
                "file": part_file,
            }
        )
        semantic_parts.append(
            {
                **part_payload,
                "body": {
                    "step_path": part_file["path"],
                    "step_sha256": part_file["sha256"],
                    "volume_mm3": volume,
                },
            }
        )
        bom_items.append(
            {
                "part_id": part_id,
                "name": part_payload.get("name"),
                "material": part_payload.get("material"),
                "quantity": len(component_paths),
                "component_paths": list(component_paths),
                "step_path": part_file["path"],
            }
        )

    _complete_export_validation(
        artifact_dir=artifact_dir,
        result_kind=result_kind,
        inventory=inventory,
        spec=spec,
        product_step_path=product_step_path,
        part_manifest_records=part_manifest_records,
        validation_report=validation_report,
    )

    if result_kind == "assembly":
        assembly_definitions = [
            inventory["assembly_definitions"][assembly_id].to_dict()
            for assembly_id in sorted(inventory["assembly_definitions"])
        ]
        root = {"item_kind": "assembly", "item_id": final_result.assembly_id}
    else:
        assembly_definitions = []
        root = {"item_kind": "part", "item_id": "model"}

    _report_phase("structured_artifact_export")
    semantic_path = artifact_dir / "model.semantic.json"
    _write_json(
        semantic_path,
        {
            "schema_version": "cadflow-semantic-model/v1",
            "result_kind": result_kind,
            "root": root,
            "assembly_definitions": assembly_definitions,
            "part_definitions": semantic_parts,
        },
    )
    bom_path = artifact_dir / "bom.json"
    _write_json(
        bom_path,
        {"schema_version": "cadflow-bom/v1", "items": bom_items},
    )
    assumptions_path = artifact_dir / "assumptions.json"
    _write_json(
        assumptions_path,
        {
            "schema_version": "cadflow-assumptions/v1",
            "assumptions": spec["assumptions"],
        },
    )
    validation_path = artifact_dir / "validation.json"
    _write_json(validation_path, validation_report)
    source_path = artifact_dir / "source.zip"
    _write_source_snapshot(source_path, sources)

    manifest = {
        "schema_version": PRODUCT_SCHEMA_VERSION,
        "result_kind": result_kind,
        "status": "Draft",
        "summary": {
            "component_count": inventory["component_count"],
            "leaf_part_count": inventory["leaf_part_count"],
            "unique_part_count": len(part_manifest_records),
            "solid_count": inventory["solid_count"],
            "volume_mm3": inventory["solid_volume"],
        },
        "files": {
            "semantic_model": _file_record(artifact_dir, semantic_path),
            "scene": _file_record(artifact_dir, scene_path),
            "product_step": _file_record(artifact_dir, product_step_path),
            "bom": _file_record(artifact_dir, bom_path),
            "validation_report": _file_record(artifact_dir, validation_path),
            "assumptions": _file_record(artifact_dir, assumptions_path),
            "source_snapshot": _file_record(artifact_dir, source_path),
        },
        "parts": part_manifest_records,
    }
    manifest_path = artifact_dir / PRODUCT_MANIFEST_NAME
    _write_json(manifest_path, manifest)
    return product_step_path, manifest


def _render_review_evidence(
    *,
    project_root: Path,
    step_path: Path,
    result_kind: str,
    component_count: int | None,
    leaf_part_count: int | None,
    solid_count: int,
    solid_volume: float,
    review_model_sha256: str,
    review_source_files: list[dict[str, str]],
    assembly_appearances: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None, str | None]:
    try:
        from cadflow.inspect import brep

        inspection = brep.inspect_step_rbrepinspection(step_path)
        review_root = project_root / ".cad-review" / review_model_sha256
        review_root.mkdir(parents=True, exist_ok=True)
        single_render_path = review_root / "isometric.png"
        contact_sheet_path = review_root / "contact-sheet.png"
        common_render_options = {
            "image_size": (8.0, 8.0),
            "dpi": 64,
            "background_color": (0.965, 0.972, 0.980),
            "show_brep_edges": True,
        }
        canonical_views = (
            (0.0, 0.0, "front"),
            (0.0, 180.0, "back"),
            (0.0, 90.0, "right"),
            (0.0, -90.0, "left"),
            (90.0, 0.0, "top"),
            (-90.0, 0.0, "bottom"),
            (35.0, -45.0, "isometric"),
            (35.0, 135.0, "isometric-rear"),
        )
        colored_component_count = sum(
            appearance.get("base_color") is not None
            for appearance in assembly_appearances or ()
        )
        if colored_component_count:
            step_components = [
                component
                for component in brep.inspect_step_components_rdescriptorlist(
                    step_path
                )
                if not component["assembly"] and int(component["solid_count"]) > 0
            ]
            if len(step_components) != len(assembly_appearances or ()):
                raise ValueError(
                    "Assembly material rendering requires one STEP component per "
                    f"leaf Part; found {len(step_components)} STEP components and "
                    f"{len(assembly_appearances or ())} leaf Parts"
                )
            component_colors = {
                str(component["node_id"]): tuple(
                    float(value)
                    for value in (
                        appearance["base_color"] or DEFAULT_REVIEW_COMPONENT_COLOR
                    )
                )
                for component, appearance in zip(
                    step_components,
                    assembly_appearances or (),
                    strict=True,
                )
            }
            colored_render_options = {
                "component_colors": component_colors,
                "image_size": (8.0, 8.0),
                "dpi": 64,
                "with_context": False,
                "show_legend": False,
            }
            brep.render_step_components_colored_rpath(
                step_path,
                output_path=single_render_path,
                views=((35.0, -45.0, "isometric"),),
                title="CAD Review - Isometric",
                **colored_render_options,
            )
            brep.render_step_components_colored_rpath(
                step_path,
                output_path=contact_sheet_path,
                views=canonical_views,
                title="CAD Review - Eight Views",
                **colored_render_options,
            )
            render_mode = (
                "scene_presentation"
                if any(
                    appearance.get("presentation_appearance") is not None
                    for appearance in assembly_appearances or ()
                )
                else "assembly_materials"
            )
        else:
            brep.render_step_views_rpath(
                step_path,
                single_render_path,
                views=((35.0, -45.0, "isometric"),),
                title="CAD Review - Isometric",
                **common_render_options,
            )
            brep.render_step_views_rpath(
                step_path,
                contact_sheet_path,
                views=canonical_views,
                title="CAD Review - Eight Views",
                **common_render_options,
            )
            render_mode = "step_xcaf"

        def image_sha256(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        manifest = {
            "schema_version": "cad-review/v1",
            "model_sha256": review_model_sha256,
            "source_files": review_source_files,
            "views": [
                {"view_id": view[2], "elevation": view[0], "azimuth": view[1]}
                for view in canonical_views
            ],
            "single_render": {
                "path": single_render_path.name,
                "image_sha256": image_sha256(single_render_path),
            },
            "contact_sheet": {
                "path": contact_sheet_path.name,
                "image_sha256": image_sha256(contact_sheet_path),
            },
            "appearance": {
                "render_mode": render_mode,
                "colored_component_count": colored_component_count,
                "components": assembly_appearances or [],
            },
            "metrics": {
                "result_kind": result_kind,
                "component_count": component_count,
                "leaf_part_count": leaf_part_count,
                "solid_count": solid_count,
                "volume_mm3": solid_volume,
                "bbox_mm": [float(value) for value in inspection.bounding_box],
                "topology": dict(inspection.counts),
                "is_valid": (
                    solid_count == 1 and solid_volume > 0.0
                    if result_kind == "part"
                    else leaf_part_count is not None
                    and leaf_part_count >= 1
                    and solid_count == leaf_part_count
                    and solid_volume > 0.0
                ),
            },
        }
        manifest_path = review_root / "manifest.json"
        _write_json(manifest_path, manifest)
        return (
            str(review_root.relative_to(project_root)),
            str(manifest_path.relative_to(project_root)),
            None,
        )
    except Exception as error:
        return None, None, type(error).__name__ + ": " + str(error)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 3:
        raise RuntimeError("CAD runner requires model, Project, and code paths")
    model_path = Path(arguments[0]).resolve()
    project_root = Path(arguments[1]).resolve()
    code_root = Path(arguments[2]).resolve()
    if not code_root.is_dir() or not project_root.is_dir():
        raise RuntimeError("Project code or working directory is missing")
    sys.path.insert(0, str(code_root))
    os.chdir(project_root)
    review_model_sha256, review_source_files, sources = _source_manifest(code_root)
    imported_modules = {"cadflow"}
    real_import = builtins.__import__

    def tracking_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        imported_modules.add(name)
        return real_import(name, globals, locals, fromlist, level)

    _report_phase("source_import")
    builtins.__import__ = tracking_import
    try:
        namespace = runpy.run_path(str(model_path), run_name="cadflow_model_source")
    finally:
        builtins.__import__ = real_import
    build_model = namespace.get("build_model")
    if not callable(build_model):
        raise RuntimeError(
            "Model Source must define build_model(model) -> cad.Shape | cad.Assembly"
        )
    presentation = _scene_presentation(namespace)

    print(
        PREFLIGHT_PREFIX
        + json.dumps(
            {"status": "passed", "imported_modules": sorted(imported_modules)[:256]},
            separators=(",", ":"),
        ),
        flush=True,
    )

    result_kind: str | None = None
    component_count: int | None = None
    leaf_part_count: int | None = None
    solid_count: int | None = None
    solid_volume: float | None = None
    topology_error: str | None = None
    review_artifact_dir: str | None = None
    review_manifest_path: str | None = None
    review_evidence_error: str | None = None
    product_manifest_path: str | None = None
    product_status: str | None = None
    unique_part_count: int | None = None
    product_validation_status: str | None = None
    product_validation_failures: list[str] = []
    product_validation_checks: list[dict[str, Any]] = []
    validation_short_circuited = False

    with cad.Model() as model:
        _report_phase("model_build")
        final_result = build_model(model)
        artifact_dir = project_root / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        inventory: dict[str, Any] | None = None
        if isinstance(final_result, cad.Shape):
            result_kind = "part"
            topology = dict(final_result.topology)
            solid_count = int(topology.get("solids", 0))
            solid_volume = float(final_result.volume)
            if solid_count == 1 and math.isfinite(solid_volume) and solid_volume > 0.0:
                component_count = 0
                leaf_part_count = 1
                inventory = {
                    "component_count": 0,
                    "leaf_part_count": 1,
                    "solid_count": 1,
                    "solid_volume": solid_volume,
                    "assembly_definitions": {},
                    "part_definitions": {"model": None},
                    "occurrences": {"model": ["model"]},
                }
        elif isinstance(final_result, cad.Assembly):
            result_kind = "assembly"
            try:
                inventory = _assembly_inventory(final_result)
                component_count = inventory["component_count"]
                leaf_part_count = inventory["leaf_part_count"]
                solid_count = inventory["solid_count"]
                solid_volume = inventory["solid_volume"]
            except (TypeError, ValueError) as error:
                topology_error = str(error)
        else:
            raise TypeError("Model Source must return one CadFlow Shape or cad.Assembly")

        if inventory is not None:
            spec, spec_failures = _product_spec(namespace)
            validation_report = _base_validation_report(
                result_kind=result_kind,
                inventory=inventory,
                spec=spec,
                spec_failures=spec_failures,
            )
            if result_kind == "assembly":
                final_result, inventory = _validate_assembly(
                    assembly=final_result,
                    validation_report=validation_report,
                )
                component_count = inventory["component_count"]
                leaf_part_count = inventory["leaf_part_count"]
                solid_count = inventory["solid_count"]
                solid_volume = inventory["solid_volume"]
            product_validation_status = validation_report["status"]
            product_validation_failures = list(
                validation_report["blocking_failures"]
            )
            product_validation_checks = list(validation_report["checks"])
            validation_short_circuited = bool(
                result_kind == "assembly"
                and _assembly_early_gate_failed(validation_report)
            )
            if validation_short_circuited:
                unique_part_count = len(inventory["part_definitions"])
                product_status = "Draft"
            else:
                product_step_path, product_manifest = _export_product_bundle(
                    artifact_dir=artifact_dir,
                    final_result=final_result,
                    result_kind=result_kind,
                    inventory=inventory,
                    spec=spec,
                    presentation=presentation,
                    validation_report=validation_report,
                    sources=sources,
                )
                product_validation_status = validation_report["status"]
                product_validation_failures = list(
                    validation_report["blocking_failures"]
                )
                product_validation_checks = list(validation_report["checks"])
                unique_part_count = product_manifest["summary"]["unique_part_count"]
                product_status = product_manifest["status"]
                product_manifest_path = str(
                    (artifact_dir / PRODUCT_MANIFEST_NAME).relative_to(project_root)
                )
                _report_phase("review_evidence")
                (
                    review_artifact_dir,
                    review_manifest_path,
                    review_evidence_error,
                ) = _render_review_evidence(
                    project_root=project_root,
                    step_path=product_step_path,
                    result_kind=result_kind,
                    component_count=component_count,
                    leaf_part_count=leaf_part_count,
                    solid_count=solid_count,
                    solid_volume=solid_volume,
                    review_model_sha256=review_model_sha256,
                    review_source_files=review_source_files,
                    assembly_appearances=(
                        _review_appearances(
                            final_result=final_result,
                            result_kind=result_kind,
                            presentation=presentation,
                        )
                    ),
                )

    payload = {
        "result_kind": result_kind,
        "final_shape_count": 1,
        "component_count": component_count,
        "leaf_part_count": leaf_part_count,
        "unique_part_count": unique_part_count,
        "solid_count": solid_count,
        "solid_volume": solid_volume,
        "review_artifact_dir": review_artifact_dir,
        "review_manifest_path": review_manifest_path,
        "review_model_sha256": review_model_sha256,
        "review_evidence_error": review_evidence_error,
        "topology_error": topology_error,
        "product_manifest_path": product_manifest_path,
        "product_status": product_status,
        "product_validation_status": product_validation_status,
        "product_validation_failures": product_validation_failures,
        "product_validation_checks": (
            _short_circuit_validation_checks(product_validation_checks)
            if validation_short_circuited
            else []
        ),
        "validation_short_circuited": validation_short_circuited,
    }
    print(RESULT_PREFIX + json.dumps(payload, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
