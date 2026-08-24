from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.cad_executor import CADExecutor
from backend.model_source import create_model_source
from backend.projects import ProjectStore


def _accepted_part_store(root: Path) -> tuple[ProjectStore, str]:
    store = ProjectStore(root)
    project = store.create_project("Product API")
    store.submit_prompt(project.project_id, "Create a test block.")
    project_dir = store.project_directory(project.project_id)
    scaffold = create_model_source(project_dir)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": ["Loads are outside this geometry-only test."],
}

def build_model(model: cad.Model):
    return model.box(width=2.0, depth=3.0, height=4.0)
""",
        encoding="utf-8",
    )
    execution = CADExecutor().execute(project_dir, timeout_seconds=30.0)
    assert execution.is_validated_product
    assert execution.review_model_sha256 is not None
    store.mark_succeeded(
        project.project_id,
        {
            "execution_result": execution.to_dict(),
            "review_result": {
                "status": "pass",
                "summary": "The block matches the request.",
                "findings": [],
                "model_sha256": execution.review_model_sha256,
                "reviewer_version": "test",
                "checked_requirements": ["block"],
                "evidence_hashes": {},
            },
        },
    )
    return store, project.project_id


def test_product_api_exposes_verified_structure_and_downloads(tmp_path: Path) -> None:
    store, project_id = _accepted_part_store(tmp_path)
    app = create_app(store=store)

    with TestClient(app) as client:
        project = client.get(f"/api/projects/{project_id}")
        product = client.get(f"/api/projects/{project_id}/product")
        manifest = client.get(f"/api/projects/{project_id}/product/manifest")
        product_step = client.get(
            f"/api/projects/{project_id}/product/files/product_step"
        )
        part_step = client.get(
            f"/api/projects/{project_id}/product/part-step",
            params={"part_id": "model"},
        )

    assert project.status_code == 200
    assert project.json()["product_available"] is True
    assert project.json()["result_kind"] == "part"
    assert project.json()["product_status"] == "Accepted"

    assert product.status_code == 200
    payload = product.json()
    assert payload["schema_version"] == "cadflow-product-api/v1"
    assert payload["result_kind"] == "part"
    assert payload["status"] == "Accepted"
    assert payload["summary"]["unique_part_count"] == 1
    assert payload["semantic_model"]["root"] == {
        "item_kind": "part",
        "item_id": "model",
    }
    assert payload["bom"][0]["part_id"] == "model"
    assert payload["assumptions"] == ["Loads are outside this geometry-only test."]
    assert payload["validation_report"]["status"] == "Accepted"
    assert payload["parts"][0]["download_url"].endswith(
        "/product/part-step?part_id=model"
    )

    assert manifest.status_code == 200
    assert manifest.json()["schema_version"] == "cadflow-product/v1"
    assert product_step.status_code == 200
    assert product_step.content.startswith(b"ISO-10303-21")
    assert part_step.status_code == 200
    assert part_step.content.startswith(b"ISO-10303-21")


def test_product_download_rejects_undeclared_roles_and_parts(tmp_path: Path) -> None:
    store, project_id = _accepted_part_store(tmp_path)
    app = create_app(store=store)

    with TestClient(app) as client:
        role = client.get(
            f"/api/projects/{project_id}/product/files/not-a-role"
        )
        part = client.get(
            f"/api/projects/{project_id}/product/part-step",
            params={"part_id": "not-a-part"},
        )

    assert role.status_code == 404
    assert part.status_code == 404
