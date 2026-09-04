"""Build material-preserving GLB previews from compiled CadFlow scenes."""

from __future__ import annotations

import copy
import json
import math
import struct
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cadflow as cad


Matrix = tuple[tuple[float, float, float, float], ...]
IDENTITY_MATRIX: Matrix = (
    (1, 0, 0, 0),
    (0, 1, 0, 0),
    (0, 0, 1, 0),
    (0, 0, 0, 1),
)


def assembly_preview_glb(
    assembly: cad.Assembly,
    *,
    deflection: float,
    presentation: Mapping[str, Any] | None = None,
) -> bytes:
    package = cad.compile_scene(
        scene_id="live-preview",
        roots=(cad.SceneRoot(root_id="main", value=assembly),),
        options=cad.SceneCompileOptions(linear_tolerance=deflection),
    )
    if presentation is not None:
        preview_presentation = dict(presentation)
        preview_presentation["source_scene_id"] = "live-preview"
        package = cad.apply_presentation(
            package=package,
            presentation=preview_presentation,
            embed_presentation=False,
        )
    return _compiled_scene_glb(package)


def shape_preview_glb(
    shape: cad.Shape,
    *,
    deflection: float,
    presentation: Mapping[str, Any] | None = None,
) -> bytes:
    if presentation is None:
        return shape.preview_glb(deflection=deflection)

    with tempfile.TemporaryDirectory(prefix="cadflow-shape-preview-") as directory:
        step_path = Path(directory) / "model.step"
        shape.export_step(str(step_path))
        scene_root = cad.Solid(cad.inspection.brep.load_step_rshape(step_path))
        package = cad.compile_scene(
            scene_id="live-preview",
            roots=(cad.SceneRoot(root_id="main", value=scene_root),),
            options=cad.SceneCompileOptions(linear_tolerance=deflection),
        )
    preview_presentation = dict(presentation)
    preview_presentation["source_scene_id"] = "live-preview"
    package = cad.apply_presentation(
        package=package,
        presentation=preview_presentation,
        embed_presentation=False,
    )
    return _compiled_scene_glb(package)


def _compiled_scene_glb(package: Any) -> bytes:
    manifest = package.manifest
    definitions = {
        value["definition_id"]: value for value in manifest["definitions"]
    }
    assets = {value["asset_id"]: value for value in manifest["geometry_assets"]}
    material_indexes = {
        appearance["appearance_id"]: index
        for index, appearance in enumerate(manifest["appearances"])
    }
    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "CadFlow live preview"},
        "scene": 0,
        "scenes": [{"name": "live-preview", "nodes": []}],
        "nodes": [],
        "meshes": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [{"byteLength": 0}],
        "materials": [
            _gltf_material(appearance) for appearance in manifest["appearances"]
        ],
    }
    binary = bytearray()
    mesh_cache: dict[tuple[str, int | None], tuple[tuple[int, Matrix], ...]] = {}
    world_transforms: dict[str, Matrix] = {}
    pending = list(manifest["nodes"])

    while pending:
        progressed = False
        for node in tuple(pending):
            parent_id = node["parent_node_id"]
            if parent_id is not None and parent_id not in world_transforms:
                continue
            parent_transform = (
                IDENTITY_MATRIX
                if parent_id is None
                else world_transforms[parent_id]
            )
            world_transform = _matrix_multiply(
                parent_transform, _placement_matrix(node["transform"])
            )
            world_transforms[node["node_id"]] = world_transform
            pending.remove(node)
            progressed = True

            definition = definitions[node["definition_id"]]
            asset_id = definition.get("geometry_asset_id")
            if not asset_id:
                continue
            appearance_id = (
                node.get("appearance_override_id") or definition.get("appearance_id")
            )
            material_index = material_indexes.get(appearance_id)
            cache_key = (asset_id, material_index)
            asset = assets[asset_id]
            if cache_key not in mesh_cache:
                source_document, source_binary = _read_glb(
                    package.blobs[asset["uri"]]
                )
                mesh_cache[cache_key] = _append_asset(
                    document,
                    binary,
                    source_document,
                    source_binary,
                    material_index,
                )

            asset_transform = _asset_matrix(asset["asset_to_scene"])
            for index, (mesh_index, source_transform) in enumerate(
                mesh_cache[cache_key]
            ):
                transform = _matrix_multiply(
                    _matrix_multiply(world_transform, asset_transform),
                    source_transform,
                )
                output_node = len(document["nodes"])
                document["nodes"].append(
                    {
                        "name": f"{node['node_id']}/{index}",
                        "mesh": mesh_index,
                        "matrix": _gltf_matrix(transform),
                    }
                )
                document["scenes"][0]["nodes"].append(output_node)

        if not progressed:
            raise ValueError("Compiled preview scene contains an invalid node hierarchy")

    if not document["meshes"]:
        raise ValueError("Compiled preview scene contains no renderable geometry")
    document["buffers"][0]["byteLength"] = len(binary)
    return _write_glb(document, bytes(binary))


def _append_asset(
    output: dict[str, Any],
    output_binary: bytearray,
    source: Mapping[str, Any],
    source_binary: bytes,
    material_index: int | None,
) -> tuple[tuple[int, Matrix], ...]:
    output_binary.extend(b"\0" * (-len(output_binary) % 4))
    binary_offset = len(output_binary)
    output_binary.extend(source_binary)
    buffer_view_offset = len(output["bufferViews"])
    accessor_offset = len(output["accessors"])
    mesh_offset = len(output["meshes"])

    for source_view in source.get("bufferViews", ()):
        view = copy.deepcopy(source_view)
        view["buffer"] = 0
        view["byteOffset"] = binary_offset + int(view.get("byteOffset", 0))
        output["bufferViews"].append(view)
    for source_accessor in source.get("accessors", ()):
        accessor = copy.deepcopy(source_accessor)
        if "bufferView" in accessor:
            accessor["bufferView"] += buffer_view_offset
        sparse = accessor.get("sparse")
        if sparse:
            sparse["indices"]["bufferView"] += buffer_view_offset
            sparse["values"]["bufferView"] += buffer_view_offset
        output["accessors"].append(accessor)
    for source_mesh in source.get("meshes", ()):
        mesh = copy.deepcopy(source_mesh)
        for primitive in mesh["primitives"]:
            primitive["attributes"] = {
                name: accessor + accessor_offset
                for name, accessor in primitive["attributes"].items()
            }
            if "indices" in primitive:
                primitive["indices"] += accessor_offset
            for target in primitive.get("targets", ()):
                for name in tuple(target):
                    target[name] += accessor_offset
            if material_index is None:
                primitive.pop("material", None)
            else:
                primitive["material"] = material_index
        output["meshes"].append(mesh)

    return tuple(
        (mesh_index + mesh_offset, transform)
        for mesh_index, transform in _mesh_instances(source)
    )


def _mesh_instances(document: Mapping[str, Any]) -> tuple[tuple[int, Matrix], ...]:
    nodes = document.get("nodes", ())
    scenes = document.get("scenes", ())
    if not scenes:
        return ()
    scene_index = int(document.get("scene", 0))
    instances: list[tuple[int, Matrix]] = []

    def visit(node_index: int, parent_transform: Matrix) -> None:
        node = nodes[node_index]
        transform = _matrix_multiply(parent_transform, _node_matrix(node))
        if "mesh" in node:
            instances.append((int(node["mesh"]), transform))
        for child in node.get("children", ()):
            visit(int(child), transform)

    for root in scenes[scene_index].get("nodes", ()):
        visit(int(root), IDENTITY_MATRIX)
    return tuple(instances)


def _node_matrix(node: Mapping[str, Any]) -> Matrix:
    if "matrix" in node:
        values = node["matrix"]
        return tuple(
            tuple(float(values[column * 4 + row]) for column in range(4))
            for row in range(4)
        )
    translation = node.get("translation", (0, 0, 0))
    rotation = node.get("rotation", (0, 0, 0, 1))
    scale = node.get("scale", (1, 1, 1))
    x, y, z, w = (float(value) for value in rotation)
    rotation_matrix: Matrix = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0),
        (0, 0, 0, 1),
    )
    return tuple(
        tuple(
            rotation_matrix[row][column] * float(scale[column])
            for column in range(3)
        )
        + (float(translation[row]),)
        for row in range(3)
    ) + ((0, 0, 0, 1),)


def _placement_matrix(transform: Mapping[str, Sequence[float]]) -> Matrix:
    origin = transform["origin"]
    x_axis = transform["x_axis"]
    y_axis = transform["y_axis"]
    z_axis = transform["z_axis"]
    return (
        (x_axis[0], y_axis[0], z_axis[0], origin[0] / 1000),
        (x_axis[1], y_axis[1], z_axis[1], origin[1] / 1000),
        (x_axis[2], y_axis[2], z_axis[2], origin[2] / 1000),
        (0, 0, 0, 1),
    )


def _asset_matrix(values: Sequence[float]) -> Matrix:
    return tuple(
        tuple(
            float(values[row * 4 + column]) / 1000
            if row < 3
            else float(values[row * 4 + column])
            for column in range(4)
        )
        for row in range(4)
    )


def _matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    return tuple(
        tuple(
            math.fsum(
                left[row][index] * right[index][column] for index in range(4)
            )
            for column in range(4)
        )
        for row in range(4)
    )


def _gltf_matrix(matrix: Matrix) -> list[float]:
    return [matrix[row][column] for column in range(4) for row in range(4)]


def _gltf_material(appearance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": appearance["appearance_id"],
        "pbrMetallicRoughness": {
            "baseColorFactor": list(appearance["base_color"]),
            "metallicFactor": appearance["metallic"],
            "roughnessFactor": appearance["roughness"],
        },
        "alphaMode": str(appearance["alpha_mode"]).upper(),
        "doubleSided": appearance["double_sided"],
    }


def _read_glb(payload: bytes) -> tuple[dict[str, Any], bytes]:
    if len(payload) < 20:
        raise ValueError("Geometry asset GLB is truncated")
    magic, version, total_length = struct.unpack_from("<4sII", payload)
    if magic != b"glTF" or version != 2 or total_length != len(payload):
        raise ValueError("Geometry asset is not a valid GLB 2.0 file")
    offset = 12
    document: dict[str, Any] | None = None
    binary = b""
    chunk_types: list[bytes] = []
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("Geometry asset GLB chunk header is truncated")
        chunk_length, chunk_type = struct.unpack_from("<I4s", payload, offset)
        offset += 8
        if offset + chunk_length > len(payload):
            raise ValueError("Geometry asset GLB chunk is truncated")
        chunk = payload[offset : offset + chunk_length]
        offset += chunk_length
        chunk_types.append(chunk_type)
        if chunk_type == b"JSON":
            document = json.loads(chunk)
        elif chunk_type == b"BIN\0":
            binary = chunk
    if document is None or chunk_types != [b"JSON", b"BIN\0"]:
        raise ValueError("Geometry asset GLB must contain one JSON and one BIN chunk")
    return document, binary


def validate_assembly_preview_glb(payload: bytes) -> None:
    document, binary = _read_glb(payload)
    expected_keys = {
        "accessors",
        "asset",
        "bufferViews",
        "buffers",
        "materials",
        "meshes",
        "nodes",
        "scene",
        "scenes",
    }
    if set(document) != expected_keys:
        raise ValueError("Material preview GLB contains unsupported resources")
    if document["asset"] != {
        "version": "2.0",
        "generator": "CadFlow live preview",
    }:
        raise ValueError("Material preview GLB has an invalid generator")
    if document["scene"] != 0 or document["scenes"] != [
        {"name": "live-preview", "nodes": list(range(len(document["nodes"])))}
    ]:
        raise ValueError("Material preview GLB has an invalid scene")

    buffers = document["buffers"]
    if not isinstance(buffers, list) or len(buffers) != 1:
        raise ValueError("Material preview GLB must contain one binary buffer")
    byte_length = buffers[0].get("byteLength")
    if (
        not isinstance(byte_length, int)
        or isinstance(byte_length, bool)
        or byte_length < 1
        or byte_length > len(binary)
        or len(binary) - byte_length > 3
    ):
        raise ValueError("Material preview GLB binary length is invalid")

    views = document["bufferViews"]
    accessors = document["accessors"]
    meshes = document["meshes"]
    materials = document["materials"]
    nodes = document["nodes"]
    collections = (views, accessors, meshes, materials, nodes)
    if not all(isinstance(values, list) for values in collections):
        raise ValueError("Material preview GLB collections are invalid")
    if not nodes or len(nodes) > 10_000 or len(meshes) > 10_000:
        raise ValueError("Material preview GLB scene is empty or too large")

    for view in views:
        offset = view.get("byteOffset", 0)
        length = view.get("byteLength")
        if (
            view.get("buffer") != 0
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(length, int)
            or isinstance(length, bool)
            or offset < 0
            or length < 1
            or offset + length > byte_length
        ):
            raise ValueError("Material preview GLB buffer view is invalid")

    total_vertices = 0
    total_indices = 0
    for node in nodes:
        if set(node) != {"name", "mesh", "matrix"}:
            raise ValueError("Material preview GLB node is invalid")
        if not isinstance(node["mesh"], int) or not 0 <= node["mesh"] < len(meshes):
            raise ValueError("Material preview GLB node mesh is invalid")
        matrix = node["matrix"]
        if (
            not isinstance(matrix, list)
            or len(matrix) != 16
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in matrix
            )
        ):
            raise ValueError("Material preview GLB node transform is invalid")

    for mesh in meshes:
        primitives = mesh.get("primitives")
        if set(mesh) != {"primitives"} or not isinstance(primitives, list) or not primitives:
            raise ValueError("Material preview GLB mesh is invalid")
        for primitive in primitives:
            if set(primitive) != {"attributes", "indices", "material", "mode"}:
                raise ValueError("Material preview GLB primitive is invalid")
            attributes = primitive["attributes"]
            if set(attributes) != {"NORMAL", "POSITION"} or primitive["mode"] != 4:
                raise ValueError("Material preview GLB must contain indexed triangles")
            position = _accessor(accessors, attributes["POSITION"], views)
            normal = _accessor(accessors, attributes["NORMAL"], views)
            indices = _accessor(accessors, primitive["indices"], views)
            if (
                position.get("componentType") != 5126
                or position.get("type") != "VEC3"
                or normal.get("componentType") != 5126
                or normal.get("type") != "VEC3"
                or normal.get("count") != position.get("count")
                or indices.get("componentType") not in (5123, 5125)
                or indices.get("type") != "SCALAR"
            ):
                raise ValueError("Material preview GLB accessor profile is invalid")
            vertex_count = position.get("count")
            index_count = indices.get("count")
            cad.scene.preflight_glb_counts("triangle", vertex_count, index_count)
            total_vertices += vertex_count
            total_indices += index_count
            material_index = primitive["material"]
            if not isinstance(material_index, int) or not 0 <= material_index < len(materials):
                raise ValueError("Material preview GLB material reference is invalid")

    cad.scene.preflight_glb_counts("triangle", total_vertices, total_indices)
    for material in materials:
        _validate_material(material)


def _accessor(
    accessors: list[dict[str, Any]], index: Any, views: list[dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(accessors):
        raise ValueError("Material preview GLB accessor reference is invalid")
    accessor = accessors[index]
    view_index = accessor.get("bufferView")
    if (
        not isinstance(view_index, int)
        or isinstance(view_index, bool)
        or not 0 <= view_index < len(views)
        or not isinstance(accessor.get("count"), int)
        or isinstance(accessor.get("count"), bool)
    ):
        raise ValueError("Material preview GLB accessor is invalid")
    return accessor


def _validate_material(material: Mapping[str, Any]) -> None:
    if set(material) != {
        "alphaMode",
        "doubleSided",
        "name",
        "pbrMetallicRoughness",
    }:
        raise ValueError("Material preview GLB material is invalid")
    pbr = material["pbrMetallicRoughness"]
    if not isinstance(pbr, dict) or set(pbr) != {
        "baseColorFactor",
        "metallicFactor",
        "roughnessFactor",
    }:
        raise ValueError("Material preview GLB PBR material is invalid")
    factors = [*pbr["baseColorFactor"], pbr["metallicFactor"], pbr["roughnessFactor"]]
    if (
        len(pbr["baseColorFactor"]) != 4
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
            for value in factors
        )
        or material["alphaMode"] not in {"OPAQUE", "MASK", "BLEND"}
        or not isinstance(material["doubleSided"], bool)
        or not isinstance(material["name"], str)
    ):
        raise ValueError("Material preview GLB material values are invalid")


def _write_glb(document: Mapping[str, Any], binary: bytes) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total_length = 28 + len(encoded) + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


__all__ = [
    "assembly_preview_glb",
    "shape_preview_glb",
    "validate_assembly_preview_glb",
]
