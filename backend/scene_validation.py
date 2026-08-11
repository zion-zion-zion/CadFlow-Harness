"""Validation of the canonical ``.scene.zip`` rendering boundary.

The first pass mirrors the browser Viewer limits and package-reference checks.
The second pass delegates the manifest, entity, and GLB contract to the
SimpleCADAPI Scene validator shipped with the installed SDK.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 10_000
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_SCENE_JSON_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_SAFE_INTEGER = 9_007_199_254_740_991

_MEMBER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$")
_HASH_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
_GLB_HEADER = struct.Struct("<4sII")
_GLB_CHUNK = struct.Struct("<II")
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942


class SceneArtifactValidationError(ValueError):
    """Raised internally when a Scene Artifact violates its public contract."""


@dataclass(frozen=True)
class SceneParseResult:
    """Safe, compact facts returned to the Agent after Scene validation."""

    valid: bool
    error: str | None = None
    schema_version: str | None = None
    member_count: int = 0
    geometry_asset_count: int = 0
    edge_asset_count: int = 0
    entity_asset_count: int = 0
    glb_asset_count: int = 0
    model_json_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "error": self.error,
            "schema_version": self.schema_version,
            "member_count": self.member_count,
            "geometry_asset_count": self.geometry_asset_count,
            "edge_asset_count": self.edge_asset_count,
            "entity_asset_count": self.entity_asset_count,
            "glb_asset_count": self.glb_asset_count,
            "model_json_present": self.model_json_present,
        }


@dataclass
class _ValidationStats:
    schema_version: str | None = None
    member_count: int = 0
    geometry_asset_count: int = 0
    edge_asset_count: int = 0
    entity_asset_count: int = 0
    glb_asset_count: int = 0
    model_json_present: bool = False

    def result(self, *, valid: bool, error: str | None = None) -> SceneParseResult:
        return SceneParseResult(
            valid=valid,
            error=error,
            schema_version=self.schema_version,
            member_count=self.member_count,
            geometry_asset_count=self.geometry_asset_count,
            edge_asset_count=self.edge_asset_count,
            entity_asset_count=self.entity_asset_count,
            glb_asset_count=self.glb_asset_count,
            model_json_present=self.model_json_present,
        )


@dataclass(frozen=True)
class _PackageRecord:
    uri: str
    byte_length: int
    content_hash: str


def validate_scene_artifact(path: str | Path) -> SceneParseResult:
    """Validate one canonical Scene Artifact without raising to the caller."""

    stats = _ValidationStats()
    try:
        artifact = Path(path)
        raw = artifact.read_bytes()
        members, manifest = _read_viewer_package(raw, stats)
        _validate_manifest_references(members, manifest, stats)
        _validate_render_assets(members, manifest)
        _validate_sdk_scene_contract(members, manifest)
    except (
        OSError,
        zipfile.BadZipFile,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RuntimeError,
        OverflowError,
        RecursionError,
    ) as exc:
        return stats.result(valid=False, error=str(exc))
    return stats.result(valid=True)


def _read_viewer_package(
    raw: bytes,
    stats: _ValidationStats,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise SceneArtifactValidationError("scene package exceeds browser size limit")
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        infos = archive.infolist()
        stats.member_count = len(infos)
        if not infos or not any(info.filename == "scene.json" for info in infos):
            raise SceneArtifactValidationError("scene.json is missing or too large")
        if len(infos) > MAX_PACKAGE_MEMBERS:
            raise SceneArtifactValidationError("scene package member count is invalid")
        seen_names: set[str] = set()
        unpacked_bytes = 0
        for info in infos:
            _validate_member_name(info.filename)
            folded_name = info.filename.lower()
            if folded_name in seen_names:
                raise SceneArtifactValidationError(
                    f"duplicate or case-colliding scene package member: {info.filename}"
                )
            seen_names.add(folded_name)
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise SceneArtifactValidationError(
                    f"unsupported ZIP compression method: {info.filename}"
                )
            if info.file_size > MAX_MEMBER_BYTES:
                raise SceneArtifactValidationError(
                    f"scene package member exceeds browser size limit: {info.filename}"
                )
            if info.filename == "scene.json" and info.file_size > MAX_SCENE_JSON_BYTES:
                raise SceneArtifactValidationError("scene.json is missing or too large")
            unpacked_bytes += info.file_size
            if unpacked_bytes > MAX_UNPACKED_BYTES:
                raise SceneArtifactValidationError(
                    "scene package expands beyond browser size limit"
                )
            if info.file_size > MAX_COMPRESSION_RATIO * max(1, info.compress_size):
                raise SceneArtifactValidationError(
                    f"scene package member compression ratio is too high: {info.filename}"
                )
        if unpacked_bytes > MAX_COMPRESSION_RATIO * max(1, len(raw)):
            raise SceneArtifactValidationError(
                "scene package compression ratio is too high"
            )

        members: dict[str, bytes] = {}
        for info in infos:
            payload = archive.read(info)
            if len(payload) != info.file_size:
                raise SceneArtifactValidationError(
                    f"scene package member length differs from ZIP metadata: {info.filename}"
                )
            members[info.filename] = payload

    try:
        manifest = json.loads(members["scene.json"].decode("utf-8"))
    except (KeyError, json.JSONDecodeError) as exc:
        raise SceneArtifactValidationError("scene.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise SceneArtifactValidationError("scene.json must contain an object manifest")
    schema_version = manifest.get("schema_version")
    stats.schema_version = schema_version if isinstance(schema_version, str) else None
    if schema_version != "1.0":
        raise SceneArtifactValidationError(f"unsupported scene schema: {schema_version}")
    return members, manifest


def _validate_manifest_references(
    members: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    stats: _ValidationStats,
) -> None:
    records: list[_PackageRecord] = []
    geometry_assets = _asset_list(manifest, "geometry_assets")
    edge_assets = _asset_list(manifest, "edge_assets")
    entity_assets = _asset_list(manifest, "entity_assets")
    stats.geometry_asset_count = len(geometry_assets)
    stats.edge_asset_count = len(edge_assets)
    stats.entity_asset_count = len(entity_assets)
    stats.glb_asset_count = stats.geometry_asset_count + stats.edge_asset_count

    for asset in geometry_assets:
        records.append(_content_addressed_asset(asset, kind="geometry", extension="glb"))
    for asset in edge_assets:
        records.append(_content_addressed_asset(asset, kind="edges", extension="glb"))
    for asset in entity_assets:
        records.append(
            _content_addressed_entity_asset(asset, extension="json")
        )

    for source_key in ("source", "presentation_source"):
        source = manifest.get(source_key)
        if source is None:
            continue
        if not isinstance(source, dict):
            raise SceneArtifactValidationError(f"{source_key} must be an object")
        embedded_uri = source.get("embedded_artifact_uri")
        if embedded_uri:
            byte_length = _required_length(
                source, "embedded_artifact_byte_length"
            )
            content_hash = source.get("artifact_hash")
            if not isinstance(content_hash, str):
                raise SceneArtifactValidationError(
                    "embedded artifact integrity metadata is missing"
                )
            records.append(
                _PackageRecord(
                    uri=embedded_uri,
                    byte_length=byte_length,
                    content_hash=content_hash,
                )
            )
        source_files = source.get("source_files", [])
        if not isinstance(source_files, list):
            raise SceneArtifactValidationError(f"{source_key}.source_files must be a list")
        for source_file in source_files:
            if not isinstance(source_file, dict):
                raise SceneArtifactValidationError("source file record must be an object")
            records.append(
                _PackageRecord(
                    uri=_required_text(source_file, "uri"),
                    byte_length=_required_length(source_file, "byte_length"),
                    content_hash=_required_text(source_file, "content_hash"),
                )
            )

    referenced = {"scene.json"}
    for record in records:
        _validate_member_name(record.uri)
        if record.uri in referenced:
            raise SceneArtifactValidationError(
                f"duplicate scene package reference: {record.uri}"
            )
        referenced.add(record.uri)
    if set(members) != referenced:
        raise SceneArtifactValidationError(
            "scene package members do not match scene.json references"
        )

    for record in records:
        payload = members[record.uri]
        if not _is_safe_length(record.byte_length) or len(payload) != record.byte_length:
            raise SceneArtifactValidationError(
                f"package member length differs from scene.json: {record.uri}"
            )
        expected = _HASH_PATTERN.fullmatch(record.content_hash)
        if expected is None:
            raise SceneArtifactValidationError(
                f"invalid package member hash: {record.uri}"
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected.group(1):
            raise SceneArtifactValidationError(
                f"package member hash differs from scene.json: {record.uri}"
            )

    stats.model_json_present = "model/model.json" in members


def _validate_render_assets(
    members: Mapping[str, bytes], manifest: Mapping[str, Any]
) -> None:
    geometry_assets = _asset_list(manifest, "geometry_assets")
    edge_assets = _asset_list(manifest, "edge_assets")
    for asset in (*geometry_assets, *edge_assets):
        uri = _required_text(asset, "uri")
        _validate_glb(members[uri], uri)
    for asset in _asset_list(manifest, "entity_assets"):
        uri = _required_text(asset, "uri")
        try:
            json.loads(members[uri].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SceneArtifactValidationError(f"entity asset is not valid JSON: {uri}") from exc
    if "model/model.json" in members:
        try:
            model = json.loads(members["model/model.json"].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SceneArtifactValidationError("model/model.json is not valid JSON") from exc
        if not isinstance(model, dict) or not isinstance(model.get("graph"), dict):
            raise SceneArtifactValidationError("model/model.json has no graph document")


def _validate_sdk_scene_contract(
    members: Mapping[str, bytes], manifest: Mapping[str, Any]
) -> None:
    try:
        from simplecadapi.scene.validation import validate_scene_package

        # The SDK validator receives the manifest-referenced blobs; the
        # transport-only ``scene.json`` member is validated by the Viewer
        # preflight above and is intentionally not one of those blobs.
        report = validate_scene_package(
            manifest,
            {uri: payload for uri, payload in members.items() if uri != "scene.json"},
        )
    except (ImportError, KeyError, TypeError, ValueError) as exc:
        raise SceneArtifactValidationError(
            "SimpleCADAPI Scene validator is unavailable"
        ) from exc
    if report.valid:
        return
    first = report.first_error
    if first is None:
        raise SceneArtifactValidationError("Scene contract validation failed")
    raise SceneArtifactValidationError(
        f"Scene contract validation failed: {first.code} at {first.path}: {first.message}"
    )


def _validate_member_name(name: str) -> None:
    if not isinstance(name, str) or not _MEMBER_PATTERN.fullmatch(name):
        raise SceneArtifactValidationError(f"invalid scene package member: {name}")
    if any(segment in {"", ".", ".."} for segment in name.split("/")):
        raise SceneArtifactValidationError(f"invalid scene package member: {name}")


def _asset_list(manifest: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    assets = manifest.get(key)
    if not isinstance(assets, list):
        raise SceneArtifactValidationError(f"scene manifest field is not a list: {key}")
    if not all(isinstance(asset, dict) for asset in assets):
        raise SceneArtifactValidationError(f"scene manifest field has invalid records: {key}")
    return assets


def _content_addressed_asset(
    asset: Mapping[str, Any], *, kind: str, extension: str
) -> _PackageRecord:
    asset_id = _required_text(asset, "asset_id")
    content_hash = _required_text(asset, "content_hash")
    if asset_id != content_hash:
        raise SceneArtifactValidationError(
            f"{kind} asset ID differs from its content hash"
        )
    match = _HASH_PATTERN.fullmatch(content_hash)
    uri = _required_text(asset, "uri")
    if match is None or uri != f"{kind}/sha256-{match.group(1)}.{extension}":
        raise SceneArtifactValidationError(
            f"invalid content-addressed {kind} asset URI"
        )
    return _PackageRecord(
        uri=uri,
        byte_length=_required_length(asset, "byte_length"),
        content_hash=content_hash,
    )


def _content_addressed_entity_asset(
    asset: Mapping[str, Any], *, extension: str
) -> _PackageRecord:
    asset_id = _required_text(asset, "entity_asset_id")
    content_hash = _required_text(asset, "content_hash")
    if asset_id != content_hash:
        raise SceneArtifactValidationError(
            "entity asset ID differs from its content hash"
        )
    match = _HASH_PATTERN.fullmatch(content_hash)
    uri = _required_text(asset, "uri")
    if match is None or uri != f"entities/sha256-{match.group(1)}.{extension}":
        raise SceneArtifactValidationError("invalid content-addressed entity asset URI")
    return _PackageRecord(
        uri=uri,
        byte_length=_required_length(asset, "byte_length"),
        content_hash=content_hash,
    )


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise SceneArtifactValidationError(f"scene manifest field is missing: {key}")
    return value


def _required_length(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_SAFE_INTEGER
    ):
        raise SceneArtifactValidationError(f"scene manifest field is invalid: {key}")
    return value


def _is_safe_length(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SAFE_INTEGER
    )


def _validate_glb(payload: bytes, uri: str) -> None:
    if len(payload) < _GLB_HEADER.size:
        raise SceneArtifactValidationError(f"GLB asset is truncated: {uri}")
    magic, version, total_length = _GLB_HEADER.unpack_from(payload)
    if magic != b"glTF" or version != 2 or total_length != len(payload):
        raise SceneArtifactValidationError(f"GLB asset is invalid: {uri}")
    cursor = _GLB_HEADER.size
    json_payload: bytes | None = None
    while cursor < len(payload):
        if cursor + _GLB_CHUNK.size > len(payload):
            raise SceneArtifactValidationError(f"GLB asset is truncated: {uri}")
        chunk_length, chunk_type = _GLB_CHUNK.unpack_from(payload, cursor)
        cursor += _GLB_CHUNK.size
        end = cursor + chunk_length
        if end > len(payload):
            raise SceneArtifactValidationError(f"GLB asset is truncated: {uri}")
        chunk = payload[cursor:end]
        if chunk_type == _JSON_CHUNK:
            if json_payload is not None:
                raise SceneArtifactValidationError(f"GLB has duplicate JSON chunks: {uri}")
            json_payload = chunk.rstrip(b" \t\r\n\x00")
        elif chunk_type != _BIN_CHUNK:
            raise SceneArtifactValidationError(f"GLB has an unknown chunk: {uri}")
        cursor = end
    if cursor != len(payload) or json_payload is None:
        raise SceneArtifactValidationError(f"GLB has no JSON chunk: {uri}")
    try:
        document = json.loads(json_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SceneArtifactValidationError(f"GLB JSON is invalid: {uri}") from exc
    if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
        raise SceneArtifactValidationError(f"GLB JSON has no glTF 2.0 asset: {uri}")


__all__ = ["SceneParseResult", "validate_scene_artifact"]
