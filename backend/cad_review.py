"""Structured, read-only review of one validated CadFlow model.

The reviewer is deliberately host-side rather than a DeepAgents subagent.  It
receives the project prompt, the exact model source, deterministic CAD facts,
and hash-bound final renders, then returns a bounded ``pass``/``fail`` result.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .cad_executor import ExecutionResult
from .model_source import model_source_digest


REVIEWER_VERSION = "cad-review-v1"
ALLOWED_CATEGORIES = frozenset(
    {
        "missing_feature",
        "dimensions",
        "alignment",
        "geometry",
        "manufacturability",
        "review_infrastructure",
    }
)
ALLOWED_SEVERITIES = frozenset({"blocking", "major", "minor"})
MAX_FINDINGS = 32
MAX_MODEL_SOURCE_CHARS = 120_000
MAX_REQUEST_CHARS = 32_000
REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "title": "cad_review",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail"]},
        "summary": {"type": "string"},
        "checked_requirements": {"type": "array", "items": {"type": "string"}},
        "findings": {
            "type": "array",
            "maxItems": MAX_FINDINGS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {"type": "string", "enum": sorted(ALLOWED_CATEGORIES)},
                    "severity": {"type": "string", "enum": sorted(ALLOWED_SEVERITIES)},
                    "requirement": {"type": "string"},
                    "observed": {"type": "string"},
                    "evidence": {"type": "object"},
                    "confidence": {"type": ["number", "null"]},
                    "recommendation": {"type": ["string", "null"]},
                },
                "required": ["category", "severity", "requirement", "observed"],
            },
        },
    },
    "required": ["status", "summary", "findings", "checked_requirements"],
}


@dataclass(frozen=True)
class ReviewFinding:
    """One bounded, actionable review observation."""

    category: str
    severity: str
    requirement: str
    observed: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewResult:
    """The persisted contract returned by the ``cad_review`` tool."""

    status: str
    summary: str
    findings: tuple[ReviewFinding, ...] = ()
    checked_requirements: tuple[str, ...] = ()
    model_sha256: str | None = None
    evidence_hashes: dict[str, str] = field(default_factory=dict)
    reviewer_version: str = REVIEWER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "checked_requirements": list(self.checked_requirements),
            "model_sha256": self.model_sha256,
            "evidence_hashes": dict(self.evidence_hashes),
            "reviewer_version": self.reviewer_version,
        }


def _finding(
    *,
    category: str,
    severity: str,
    requirement: str,
    observed: str,
    recommendation: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    confidence: float | None = None,
) -> ReviewFinding:
    if category not in ALLOWED_CATEGORIES:
        category = "geometry"
    if severity not in ALLOWED_SEVERITIES:
        severity = "blocking"
    if confidence is not None:
        confidence = max(0.0, min(float(confidence), 1.0))
    return ReviewFinding(
        category=category,
        severity=severity,
        requirement=requirement[:1000],
        observed=observed[:2000],
        evidence=dict(evidence or {}),
        confidence=confidence,
        recommendation=recommendation[:1000] if recommendation else None,
    )


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_review_root(project_dir: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    root = project_dir.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _read_manifest(
    project_dir: Path,
    execution_result: ExecutionResult,
) -> tuple[dict[str, Any] | None, Path | None, list[ReviewFinding]]:
    manifest_path = _safe_review_root(project_dir, execution_result.review_manifest_path)
    review_root = _safe_review_root(project_dir, execution_result.review_artifact_dir)
    if manifest_path is None or review_root is None:
        return None, None, [
            _finding(
                category="review_infrastructure",
                severity="blocking",
                requirement="Final CAD review evidence exists and is hash-bound.",
                observed="The CAD executor did not report a safe review manifest path.",
                recommendation="Run validate_model again to generate final review evidence.",
            )
        ]
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None, review_root, [
            _finding(
                category="review_infrastructure",
                severity="blocking",
                requirement="Final CAD review evidence exists and is hash-bound.",
                observed="The review manifest is missing.",
                evidence={"path": str(manifest_path)},
            )
        ]
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, review_root, [
            _finding(
                category="review_infrastructure",
                severity="blocking",
                requirement="The review manifest is valid JSON.",
                observed=f"The review manifest could not be read: {type(error).__name__}.",
                evidence={"path": str(manifest_path)},
            )
        ]
    if not isinstance(payload, dict):
        return None, review_root, [
            _finding(
                category="review_infrastructure",
                severity="blocking",
                requirement="The review manifest is a JSON object.",
                observed="The review manifest has an invalid top-level value.",
            )
        ]
    return payload, review_root, []


def _verify_images(
    manifest: Mapping[str, Any],
    review_root: Path,
) -> tuple[dict[str, str], list[ReviewFinding]]:
    hashes: dict[str, str] = {}
    findings: list[ReviewFinding] = []
    for key, label in (("single_render", "single isometric render"), ("contact_sheet", "eight-view contact sheet")):
        metadata = manifest.get(key)
        if not isinstance(metadata, dict):
            findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement=f"A {label} is available for review.",
                    observed=f"Manifest metadata for {label} is missing.",
                )
            )
            continue
        filename = metadata.get("path")
        expected = metadata.get("image_sha256")
        if not isinstance(filename, str) or Path(filename).name != filename:
            findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement=f"The {label} path stays inside its review revision.",
                    observed=f"Manifest path for {label} is invalid.",
                )
            )
            continue
        path = review_root / filename
        if path.is_symlink() or not path.is_file():
            findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement=f"A {label} is available for review.",
                    observed=f"The {label} file is missing.",
                    evidence={"path": str(path)},
                )
            )
            continue
        actual = _hash_file(path)
        if not isinstance(expected, str) or actual != expected:
            findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement=f"The {label} matches the review manifest.",
                    observed=f"The {label} hash does not match the manifest.",
                    evidence={"path": str(path), "expected": expected, "actual": actual},
                )
            )
            continue
        hashes[key] = actual
    return hashes, findings


def _deterministic_findings(
    execution_result: ExecutionResult,
    manifest: Mapping[str, Any] | None,
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    if execution_result.status != "succeeded":
        findings.append(
            _finding(
                category="geometry",
                severity="blocking",
                requirement="The final CAD execution succeeds.",
                observed=execution_result.error or execution_result.status,
                evidence={"error_type": execution_result.error_type},
            )
        )
        return findings
    result_kind = execution_result.result_kind or "part"
    if result_kind == "part":
        if execution_result.solid_count != 1 or execution_result.final_shape_count != 1:
            findings.append(
                _finding(
                    category="geometry",
                    severity="blocking",
                    requirement="A part result contains exactly one solid Shape.",
                    observed=f"shape_count={execution_result.final_shape_count}, solid_count={execution_result.solid_count}",
                )
            )
    elif (
        execution_result.final_shape_count != 1
        or execution_result.component_count is None
        or execution_result.component_count < 1
        or execution_result.leaf_part_count is None
        or execution_result.leaf_part_count < 1
        or execution_result.solid_count != execution_result.leaf_part_count
    ):
        findings.append(
            _finding(
                category="geometry",
                severity="blocking",
                requirement="An assembly contains valid one-solid leaf Parts.",
                observed=(
                    f"component_count={execution_result.component_count}, "
                    f"leaf_part_count={execution_result.leaf_part_count}, "
                    f"solid_count={execution_result.solid_count}"
                ),
            )
        )
    if execution_result.solid_volume is None or execution_result.solid_volume <= 0:
        findings.append(
            _finding(
                category="geometry",
                severity="blocking",
                requirement="The result has positive volume.",
                observed=f"volume={execution_result.solid_volume}",
            )
        )
    if not execution_result.scene_parse_result.valid:
        findings.append(
            _finding(
                category="geometry",
                severity="blocking",
                requirement="The final Scene Artifact is valid.",
                observed=execution_result.scene_parse_result.error or "invalid scene",
            )
        )
    if manifest is not None:
        metrics = manifest.get("metrics")
        if not isinstance(metrics, dict) or not isinstance(metrics.get("bbox_mm"), list):
            findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement="The review includes deterministic CAD metrics.",
                    observed="The bounding-box metrics are missing or malformed.",
                )
            )
    return findings


def _default_reviewer_factory(settings: Any) -> Any:
    from langchain_openai import ChatOpenAI

    arguments: dict[str, Any] = {
        "model": settings.model_id,
        "api_key": settings.api_key,
        "max_retries": 1,
        "timeout": 120,
        "use_responses_api": settings.use_responses_api,
    }
    if getattr(settings, "base_url", None):
        arguments["base_url"] = settings.base_url
    if getattr(settings, "reasoning_effort", None):
        arguments["reasoning_effort"] = settings.reasoning_effort
    return ChatOpenAI(**arguments)


def _image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _review_prompt(
    request_text: str,
    model_source: str,
    manifest: Mapping[str, Any],
) -> str:
    return (
        "Review the generated CAD model against the user's request. "
        "Only report explicit missing features, visible dimensions, alignment, "
        "geometry, or manufacturability conflicts. Do not invent requirements. "
        "Return JSON with status (pass or fail), summary, findings, and "
        "checked_requirements. Any clear conflict is fail; otherwise pass.\n\n"
        f"USER REQUEST:\n{request_text[:MAX_REQUEST_CHARS]}\n\n"
        f"MODEL SOURCE:\n{model_source[:MAX_MODEL_SOURCE_CHARS]}\n\n"
        f"DETERMINISTIC EVIDENCE:\n{json.dumps(manifest, sort_keys=True)}"
    )


def _coerce_model_result(raw: Any) -> tuple[str, str, list[ReviewFinding], tuple[str, ...]]:
    if hasattr(raw, "model_dump"):
        payload = raw.model_dump()
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        content = getattr(raw, "content", raw)
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        if not isinstance(content, str):
            raise ValueError("reviewer returned no structured content")
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("reviewer response was not JSON")
        payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict) or payload.get("status") not in {"pass", "fail"}:
        raise ValueError("reviewer returned an invalid status")
    findings: list[ReviewFinding] = []
    for item in payload.get("findings", [])[:MAX_FINDINGS] if isinstance(payload.get("findings", []), list) else []:
        if not isinstance(item, Mapping):
            continue
        findings.append(
            _finding(
                category=str(item.get("category", "geometry")),
                severity=str(item.get("severity", "major")),
                requirement=str(item.get("requirement", "User requirement")),
                observed=str(item.get("observed", item.get("message", "Review finding"))),
                evidence=item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {},
                confidence=item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
                recommendation=item.get("recommendation") if isinstance(item.get("recommendation"), str) else None,
            )
        )
    checked = payload.get("checked_requirements", [])
    checked_requirements = tuple(str(item)[:1000] for item in checked[:64]) if isinstance(checked, list) else ()
    summary = str(payload.get("summary") or ("Review passed." if payload["status"] == "pass" else "Review failed."))
    return str(payload["status"]), summary[:2000], findings, checked_requirements


def _model_findings(
    *,
    settings: Any,
    request_text: str,
    model_source: str,
    manifest: Mapping[str, Any],
    review_root: Path,
    reviewer_factory: Callable[[Any], Any] | None,
    reviewer_callbacks: Sequence[Any] | None,
) -> tuple[str, str, list[ReviewFinding], tuple[str, ...]]:
    if settings is None:
        raise RuntimeError("reviewer model settings are not configured")
    factory = reviewer_factory or _default_reviewer_factory
    reviewer = factory(settings)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an independent CAD reviewer. Use the supplied source, "
                "metrics, and images. Return only the requested structured result."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _review_prompt(request_text, model_source, manifest)},
                {"type": "text", "text": "Single isometric evidence:"},
                {"type": "image_url", "image_url": {"url": _image_data_uri(review_root / manifest["single_render"]["path"])}},
                {"type": "text", "text": "Eight-view contact-sheet evidence:"},
                {"type": "image_url", "image_url": {"url": _image_data_uri(review_root / manifest["contact_sheet"]["path"])}},
            ],
        },
    ]
    invoke_config: dict[str, Any] | None = None
    if reviewer_callbacks:
        invoke_config = {
            "callbacks": list(reviewer_callbacks),
            "metadata": {"agent_role": "reviewer"},
            "tags": ["cad-reviewer"],
        }
    runnable = (
        reviewer.with_structured_output(REVIEW_JSON_SCHEMA)
        if hasattr(reviewer, "with_structured_output")
        else reviewer
    )
    response = (
        runnable.invoke(messages, config=invoke_config)
        if invoke_config is not None
        else runnable.invoke(messages)
    )
    return _coerce_model_result(response)


def review_cad(
    *,
    project_dir: str | Path,
    request_text: str,
    model_source: str,
    execution_result: ExecutionResult,
    settings: Any = None,
    reviewer_factory: Callable[[Any], Any] | None = None,
    reviewer_callbacks: Sequence[Any] | None = None,
) -> ReviewResult:
    """Review one validated model and persist only the review result."""

    project_path = Path(project_dir).expanduser().resolve()
    manifest, review_root, evidence_findings = _read_manifest(project_path, execution_result)
    evidence_hashes: dict[str, str] = {}
    if manifest is not None and review_root is not None:
        evidence_hashes, image_findings = _verify_images(manifest, review_root)
        evidence_findings.extend(image_findings)
        if manifest.get("model_sha256") != execution_result.review_model_sha256:
            evidence_findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement="Evidence is bound to the current Model Source revision.",
                    observed="The manifest model hash does not match the execution result.",
                )
            )
        try:
            current_source_digest = model_source_digest(project_path)
        except OSError as error:
            evidence_findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement="The complete current Model Source can be hashed.",
                    observed=f"Source hashing failed: {type(error).__name__}.",
                )
            )
        else:
            if current_source_digest != execution_result.review_model_sha256:
                evidence_findings.append(
                    _finding(
                        category="review_infrastructure",
                        severity="blocking",
                        requirement="Evidence covers every current Python source file.",
                        observed="Project Python sources changed after CAD execution.",
                    )
                )
    findings = _deterministic_findings(execution_result, manifest)
    findings.extend(evidence_findings)
    checked = (
        "User-requested CAD features",
        "Visible dimensions and proportions",
        "Alignment and orientation",
        "Basic solid geometry validity",
    )
    summary = "Deterministic review failed."
    if not any(f.severity == "blocking" for f in findings) and manifest is not None and review_root is not None:
        try:
            status, summary, model_findings, model_checked = _model_findings(
                settings=settings,
                request_text=request_text,
                model_source=model_source,
                manifest=manifest,
                review_root=review_root,
                reviewer_factory=reviewer_factory,
                reviewer_callbacks=reviewer_callbacks,
            )
            findings.extend(model_findings)
            checked = model_checked or checked
        except Exception as error:
            status = "fail"
            findings.append(
                _finding(
                    category="review_infrastructure",
                    severity="blocking",
                    requirement="An independent structured CAD review completes.",
                    observed=f"Reviewer failed: {type(error).__name__}: {error}",
                    recommendation="Retry cad_review after the reviewer service is available.",
                )
            )
        else:
            if status == "fail":
                summary = summary or "Reviewer found conflicts with the request."
    else:
        status = "fail"
    if any(f.severity in {"blocking", "major"} for f in findings):
        status = "fail"
    elif status != "pass":
        status = "fail"
    if status == "pass":
        summary = summary or "Review passed."
    result = ReviewResult(
        status=status,
        summary=summary[:2000],
        findings=tuple(findings[:MAX_FINDINGS]),
        checked_requirements=tuple(checked),
        model_sha256=execution_result.review_model_sha256,
        evidence_hashes=evidence_hashes,
    )
    if review_root is not None:
        review_root.mkdir(parents=True, exist_ok=True)
        temporary = review_root / ".result.json.tmp"
        temporary.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(review_root / "result.json")
    return result


__all__ = [
    "ALLOWED_CATEGORIES",
    "ALLOWED_SEVERITIES",
    "REVIEWER_VERSION",
    "REVIEW_JSON_SCHEMA",
    "ReviewFinding",
    "ReviewResult",
    "review_cad",
]
