"""Versioned CADTestBench suite loading and deterministic prompt contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SUITE_PATH = Path(__file__).resolve().parent / "suites" / "smoke-5.json"
NORMALIZATION_VERSION = "cadflow-product-prompt/v1"
FORBIDDEN_PROMPT_TERMS = (
    "cadquery",
    "python",
    "code",
    "cad.shape",
    "cad.assembly",
    "api",
    "function",
    "class",
    "module",
    "library",
    "sketch",
    "extrude",
    "negative extrusion",
    "call the",
)


@dataclass(frozen=True)
class SuiteSample:
    sample_id: str
    source_prompt: str
    product_prompt: str
    cadtest_ids: tuple[int, ...]
    reference_path: str
    source_prompt_sha256: str
    product_prompt_sha256: str
    input_sha256: str
    dataset_revision: str
    normalization_version: str
    unit: str
    scale_factor: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_prompt": self.source_prompt,
            "product_prompt": self.product_prompt,
            "cadtest_ids": list(self.cadtest_ids),
            "reference_path": self.reference_path,
            "source_prompt_sha256": self.source_prompt_sha256,
            "product_prompt_sha256": self.product_prompt_sha256,
            "input_sha256": self.input_sha256,
            "dataset_revision": self.dataset_revision,
            "normalization_version": self.normalization_version,
            "unit": self.unit,
            "scale_factor": self.scale_factor,
        }


@dataclass(frozen=True)
class BenchmarkSuite:
    suite_id: str
    manifest_path: Path
    dataset: dict[str, Any]
    normalization: dict[str, Any]
    samples: tuple[SuiteSample, ...]
    manifest_sha256: str

    @property
    def scale_factor(self) -> float:
        return float(self.normalization["scale_factor"])

    def sample(self, sample_id: str) -> SuiteSample:
        for item in self.samples:
            if item.sample_id == sample_id:
                return item
        raise KeyError(f"unknown suite sample: {sample_id}")


def load_suite(path: str | Path = SUITE_PATH) -> BenchmarkSuite:
    manifest_path = Path(path).expanduser().resolve()
    raw_bytes = manifest_path.read_bytes()
    document = json.loads(raw_bytes)
    if not isinstance(document, Mapping):
        raise ValueError("suite manifest must be a JSON object")
    if document.get("schema_version") != "cadflow-benchmark-suite/v1":
        raise ValueError("unsupported suite manifest schema")
    normalization = document.get("normalization")
    dataset = document.get("dataset")
    raw_samples = document.get("samples")
    if not isinstance(normalization, Mapping) or not isinstance(dataset, Mapping):
        raise ValueError("suite manifest requires dataset and normalization objects")
    dataset_revision = _required_string(dataset, "revision")
    if normalization.get("version") != NORMALIZATION_VERSION:
        raise ValueError("unsupported prompt normalization version")
    scale = normalization.get("scale_factor")
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale <= 0:
        raise ValueError("suite scale_factor must be a positive number")
    unit = _required_string(normalization, "unit")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("suite manifest must contain samples")

    samples: list[SuiteSample] = []
    seen: set[str] = set()
    for raw in raw_samples:
        if not isinstance(raw, Mapping):
            raise ValueError("suite samples must be objects")
        sample_id = _required_string(raw, "sample_id")
        if sample_id in seen:
            raise ValueError(f"duplicate suite sample: {sample_id}")
        seen.add(sample_id)
        source_prompt = _required_string(raw, "source_prompt")
        product_prompt = _required_string(raw, "product_prompt")
        test_ids = raw.get("cadtest_ids")
        if not isinstance(test_ids, list) or not test_ids or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 1
            for item in test_ids
        ):
            raise ValueError(f"{sample_id}: cadtest_ids must be positive integers")
        source_hash = _sha256_text(source_prompt)
        if raw.get("source_prompt_sha256") != source_hash:
            raise ValueError(f"{sample_id}: source prompt hash does not match")
        validate_product_prompt(product_prompt)
        product_hash = _sha256_text(product_prompt)
        if raw.get("product_prompt_sha256", product_hash) != product_hash:
            raise ValueError(f"{sample_id}: product prompt hash does not match")
        input_hash = _input_hash(
            source_prompt,
            product_prompt,
            normalization_version=NORMALIZATION_VERSION,
            scale_factor=float(scale),
        )
        samples.append(
            SuiteSample(
                sample_id=sample_id,
                source_prompt=source_prompt,
                product_prompt=product_prompt,
                cadtest_ids=tuple(test_ids),
                reference_path=_required_string(raw, "reference_path"),
                source_prompt_sha256=source_hash,
                product_prompt_sha256=product_hash,
                input_sha256=input_hash,
                dataset_revision=dataset_revision,
                normalization_version=NORMALIZATION_VERSION,
                unit=unit,
                scale_factor=float(scale),
            )
        )
    return BenchmarkSuite(
        suite_id=_required_string(document, "suite_id"),
        manifest_path=manifest_path,
        dataset=dict(dataset),
        normalization=dict(normalization),
        samples=tuple(samples),
        manifest_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def normalize_prompt(source_prompt: str, *, scale_factor: float) -> str:
    """Normalize a simple CADTestBench prompt without model inference.

    The smoke suite stores reviewed normalized text. This helper is intentionally
    conservative and is used for audit/tests and future sample preparation; it
    never runs during a benchmark to rewrite the prompt sent to the model.
    """

    if not isinstance(source_prompt, str) or not source_prompt.strip():
        raise ValueError("source prompt must be non-empty")
    text = source_prompt.strip()
    text = re.sub(r"^Write Python code using CADQuery to create\s+", "Create ", text, flags=re.I)
    text = re.sub(r"\bunits?\b", "mm", text, flags=re.I)
    text = re.sub(r"\b(approximately|approx\.?|about)\s+", "", text, flags=re.I)
    text = re.sub(r"\bthen\b", "and", text, flags=re.I)
    text = re.sub(r"\b(sketch|extrud(?:e|ed|ing|s|ion))\b", "shape", text, flags=re.I)
    text = re.sub(r"\bPython\b|\bCADQuery\b|\bcode\b", "", text, flags=re.I)
    numbers = re.compile(r"(?<![A-Za-z])(-?\d+(?:\.\d+)?)(?![A-Za-z])")
    text = numbers.sub(lambda match: _format_number(float(match.group(1)) * scale_factor), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def validate_product_prompt(prompt: str) -> None:
    lowered = prompt.casefold()
    found = [term for term in FORBIDDEN_PROMPT_TERMS if term in lowered]
    if found:
        raise ValueError("product prompt contains implementation terms: " + ", ".join(found))


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"suite manifest field {key!r} must be a non-empty string")
    return item.strip()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _input_hash(source: str, product: str, *, normalization_version: str, scale_factor: float) -> str:
    payload = json.dumps(
        {
            "normalization_version": normalization_version,
            "product_prompt": product,
            "scale_factor": scale_factor,
            "source_prompt": source,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(payload)


def _format_number(value: float) -> str:
    return f"{value:.12g}"


__all__ = [
    "BenchmarkSuite",
    "FORBIDDEN_PROMPT_TERMS",
    "NORMALIZATION_VERSION",
    "SUITE_PATH",
    "SuiteSample",
    "load_suite",
    "normalize_prompt",
    "validate_product_prompt",
]
