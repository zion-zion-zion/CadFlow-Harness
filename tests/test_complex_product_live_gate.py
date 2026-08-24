from __future__ import annotations

import io
import json
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent import MAX_AGENT_RUN_SECONDS
from backend.app import create_app
from backend.projects import ProjectState
from backend.scene_validation import validate_scene_artifact


@dataclass(frozen=True)
class _GateTask:
    category: str
    name: str
    prompt: str
    result_kind: str
    part_quantities: dict[str, int]
    max_size_mm: tuple[float, float, float] | None
    min_python_files: int


_ASSEMBLY_SUFFIX = """

这是复杂产品任务。返回真实 cad.Assembly，不得融合成单个 Shape。使用多个职责明确的
Python 模块组织共享尺寸、零件族和最终装配，model.py 只保留稳定入口和 PRODUCT_SPEC。
只使用公开 CadFlow API。所有叶子必须是独立的一体 cad.Part；添加物理连接器、关节和
传动耦合，严格求解约束。重复件必须复用同一 Part 定义。只对真实接触或配合的具名组件
对声明碰撞排除并说明物理理由。记录未执行的强度、寿命、公差等工程分析假设。
"""


_TASKS = (
    _GateTask(
        category="parallel_axis",
        name="Parallel-axis spur reducer",
        prompt="""
设计一个单级平行轴直齿圆柱齿轮减速器。输入齿轮 18 齿、输出齿轮 36 齿，模数
1.25 mm、压力角 20 度、齿宽 8 mm，理论减速比 2:1。两根直径 8 mm 的独立轴由一个
带安装孔的刚性底座支承；齿轮必须有真实轴孔，轴线平行，并建立反向齿轮耦合。产品
包络不得超过 105 x 80 x 45 mm。制造件清单必须恰好使用这些 Part ID，且各出现一次：
input_gear、output_gear、input_shaft、output_shaft、base。
""" + _ASSEMBLY_SUFFIX,
        result_kind="assembly",
        part_quantities={
            "input_gear": 1,
            "output_gear": 1,
            "input_shaft": 1,
            "output_shaft": 1,
            "base": 1,
        },
        max_size_mm=(105.0, 80.0, 45.0),
        min_python_files=4,
    ),
    _GateTask(
        category="planetary",
        name="Single-stage planetary reducer",
        prompt="""
设计一个单级同轴行星减速器：太阳轮 12 齿、三个相同的行星轮各 18 齿、内齿圈
48 齿，模数 1.25 mm、压力角 20 度、齿宽 8 mm。内齿圈固定，太阳轮输入，行星架
输出，理论减速比 5:1；三个行星轮必须等角布置并复用一个 Part 定义。使用独立输入轴、
输出轴和固定框架，建立所需关节与运动耦合。产品包络不得超过 110 x 110 x 55 mm。
制造件清单必须恰好使用这些 Part ID 和数量：sun_gear 1、planet_gear 3、ring_gear 1、
carrier 1、input_shaft 1、output_shaft 1、frame 1。
""" + _ASSEMBLY_SUFFIX,
        result_kind="assembly",
        part_quantities={
            "sun_gear": 1,
            "planet_gear": 3,
            "ring_gear": 1,
            "carrier": 1,
            "input_shaft": 1,
            "output_shaft": 1,
            "frame": 1,
        },
        max_size_mm=(110.0, 110.0, 55.0),
        min_python_files=4,
    ),
    _GateTask(
        category="belt_drive",
        name="Open-belt reduction stage",
        prompt="""
设计一个开放式平行轴皮带减速传动。主动轮节圆直径 24 mm，从动轮节圆直径
48 mm，中心距 65 mm，理论减速比 2:1 且同向转动。两个带轮各有直径 8.2 mm 的真实
通孔，并分别装在直径 8 mm 的独立轴上。皮带必须是一个连续的独立实体 Part，而不是
标签或运动约束的替代品；建立同向皮带耦合。使用一个带四个安装孔的固定底座支承两轴。
产品包络不得超过 125 x 85 x 45 mm。制造件清单必须恰好使用这些 Part ID，且各出现
一次：driver_pulley、driven_pulley、belt、input_shaft、output_shaft、base。
""" + _ASSEMBLY_SUFFIX,
        result_kind="assembly",
        part_quantities={
            "driver_pulley": 1,
            "driven_pulley": 1,
            "belt": 1,
            "input_shaft": 1,
            "output_shaft": 1,
            "base": 1,
        },
        max_size_mm=(125.0, 85.0, 45.0),
        min_python_files=4,
    ),
    _GateTask(
        category="right_angle",
        name="Right-angle bevel reducer",
        prompt="""
设计一对 90 度相交轴直齿锥齿轮减速传动。输入小齿轮 20 齿、输出大齿轮 40 齿，
模数 1.0 mm、压力角 20 度、面宽 7 mm，按齿数计算互补节锥角并实现 2:1 减速。
两根直径 8 mm 的独立轴必须互相垂直，由一个带安装孔的 L 形固定支架支承；两个齿轮
有真实轴孔，并建立反向齿轮耦合。产品包络不得超过 90 x 90 x 70 mm。制造件清单
必须恰好使用这些 Part ID，且各出现一次：pinion、gear、input_shaft、output_shaft、mount。
""" + _ASSEMBLY_SUFFIX,
        result_kind="assembly",
        part_quantities={
            "pinion": 1,
            "gear": 1,
            "input_shaft": 1,
            "output_shaft": 1,
            "mount": 1,
        },
        max_size_mm=(90.0, 90.0, 70.0),
        min_python_files=4,
    ),
    _GateTask(
        category="single_part_regression",
        name="Handled ceramic cup",
        prompt="""
创建一个单独制造的一体陶瓷杯 Shape：杯身外径 82 mm、总高 96 mm、壁厚 3.5 mm、
底厚 5 mm，顶部完全敞开；侧面有与杯身融合的 C 形实心把手，底部有融合的环形圈足。
所有连接必须形成恰好一个有效正体积 solid，不得返回 Assembly。记录圆角半径等非关键
推断，但不得改变给定主尺寸。
""",
        result_kind="part",
        part_quantities={"model": 1},
        max_size_mm=None,
        min_python_files=1,
    ),
)


def _wait_for_terminal(
    client: TestClient,
    project_id: str,
) -> dict[str, object]:
    deadline = time.monotonic() + MAX_AGENT_RUN_SECONDS + 60.0
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200
        project = response.json()
        if project["state"] in {
            ProjectState.SUCCEEDED.value,
            ProjectState.FAILED.value,
            ProjectState.STOPPED.value,
        }:
            return project
        time.sleep(0.5)
    raise AssertionError(f"live complex-product task did not finish: {project_id}")


def _hidden_oracle(
    client: TestClient,
    project_id: str,
    task: _GateTask,
    task_root: Path,
) -> list[str]:
    issues: list[str] = []
    response = client.get(f"/api/projects/{project_id}/product")
    if response.status_code != 200:
        return [f"product API returned {response.status_code}"]
    product = response.json()
    if product.get("status") != "Accepted":
        issues.append("product status is not Accepted")
    if product.get("result_kind") != task.result_kind:
        issues.append(f"expected {task.result_kind} result")

    bom_quantities = {
        item.get("part_id"): item.get("quantity") for item in product.get("bom", [])
    }
    if bom_quantities != task.part_quantities:
        issues.append(
            f"BOM differs: expected {task.part_quantities}, observed {bom_quantities}"
        )
    summary = product.get("summary", {})
    if summary.get("unique_part_count") != len(task.part_quantities):
        issues.append("unique Part count differs from the hidden inventory")
    if summary.get("leaf_part_count") != sum(task.part_quantities.values()):
        issues.append("leaf Part count differs from the hidden inventory")

    semantic = product.get("semantic_model") or {}
    root = semantic.get("root") or {}
    if root.get("item_kind") != task.result_kind:
        issues.append("semantic root kind differs from the requested result")

    report = product.get("validation_report") or {}
    checks = {
        check.get("check_id"): check for check in report.get("checks", [])
    }
    bad_checks = {
        check_id: check.get("status")
        for check_id, check in checks.items()
        if check.get("status") not in {"passed", "not_applicable"}
    }
    if report.get("status") != "Accepted" or report.get("blocking_failures"):
        issues.append("Accepted validation report contains blocking failures")
    if bad_checks:
        issues.append(f"validation contains non-passing checks: {bad_checks}")
    for required in ("leaf_geometry", "step_export_replay", "scene_parse", "independent_review"):
        if checks.get(required, {}).get("status") != "passed":
            issues.append(f"required check did not pass: {required}")
    if task.result_kind == "assembly":
        for required in (
            "strict_constraint_solve",
            "constraint_residuals",
            "current_pose_collision",
            "envelope",
        ):
            if checks.get(required, {}).get("status") != "passed":
                issues.append(f"Assembly check did not pass: {required}")
        envelope = checks.get("envelope", {}).get("evidence", {})
        actual_size = envelope.get("actual_size_mm")
        if not isinstance(actual_size, list) or len(actual_size) != 3:
            issues.append("Assembly envelope has no measured size")
        elif task.max_size_mm is not None and any(
            float(actual) > maximum + 1e-6
            for actual, maximum in zip(actual_size, task.max_size_mm, strict=True)
        ):
            issues.append(
                f"measured size {actual_size} exceeds hidden envelope {task.max_size_mm}"
            )

    source = client.get(
        f"/api/projects/{project_id}/product/files/source_snapshot"
    )
    if source.status_code != 200:
        issues.append("source snapshot is not downloadable")
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(source.content)) as archive:
                python_files = sorted(
                    name for name in archive.namelist() if name.endswith(".py")
                )
        except zipfile.BadZipFile:
            python_files = []
            issues.append("source snapshot is not a valid ZIP")
        if "code/model.py" not in python_files:
            issues.append("source snapshot has no stable code/model.py entry")
        if len(python_files) < task.min_python_files:
            issues.append(
                f"expected at least {task.min_python_files} Python source files, "
                f"observed {len(python_files)}"
            )

    scene = client.get(f"/api/projects/{project_id}/scene")
    if scene.status_code != 200:
        issues.append("canonical Scene is not downloadable")
    else:
        scene_path = task_root / "downloaded.scene.zip"
        scene_path.write_bytes(scene.content)
        parsed = validate_scene_artifact(scene_path)
        if not parsed.valid or parsed.glb_asset_count < 1:
            issues.append("canonical Scene did not pass an independent parse")
    if not product.get("assumptions"):
        issues.append("product has no recorded engineering assumptions")
    return issues


@pytest.mark.live_agent
def test_live_complex_product_automatic_acceptance_gate(tmp_path: Path) -> None:
    """Run prompts that are never mounted into the Agent's /skills-only view."""

    requested = {
        value.strip()
        for value in os.environ.get("CADFLOW_LIVE_GATE_CATEGORIES", "").split(",")
        if value.strip()
    }
    known_categories = {task.category for task in _TASKS}
    assert requested <= known_categories, f"unknown live-gate categories: {requested - known_categories}"
    tasks = tuple(
        task for task in _TASKS if not requested or task.category in requested
    )
    app = create_app(projects_root=tmp_path / "projects")
    client = TestClient(app)
    results: list[dict[str, object]] = []
    for index, task in enumerate(tasks, start=1):
        print(f"LIVE_GATE start {index}/{len(tasks)} {task.category}", flush=True)
        created = client.post("/api/projects", json={"name": task.name})
        assert created.status_code == 201
        project_id = created.json()["project_id"]
        started = client.post(
            f"/api/projects/{project_id}/run",
            json={"prompt": task.prompt},
        )
        assert started.status_code == 202
        project = _wait_for_terminal(client, project_id)
        task_root = tmp_path / f"gate-{index:02d}-{task.category}"
        task_root.mkdir()
        oracle_issues = (
            _hidden_oracle(client, project_id, task, task_root)
            if project["state"] == ProjectState.SUCCEEDED.value
            else []
        )
        results.append(
            {
                "category": task.category,
                "name": task.name,
                "state": project["state"],
                "failure_reason": project.get("failure_reason"),
                "accepted": project["state"] == ProjectState.SUCCEEDED.value,
                "oracle_passed": not oracle_issues,
                "false_accepted": bool(oracle_issues),
                "oracle_issues": oracle_issues,
            }
        )
        print(
            f"LIVE_GATE finish {task.category} state={project['state']} "
            f"oracle_issues={len(oracle_issues)}",
            flush=True,
        )
        assert app.state.run_coordinator.wait_for_idle(5.0)

    report = {
        "schema_version": "cadflow-live-complex-gate/v1",
        "selected_categories": sorted({task.category for task in tasks}),
        "accepted_count": sum(bool(item["accepted"]) for item in results),
        "task_count": len(results),
        "accepted_rate": sum(bool(item["accepted"]) for item in results)
        / len(results),
        "results": results,
    }
    (tmp_path / "live-complex-gate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    false_accepted = [item for item in results if item["false_accepted"]]
    assert false_accepted == [], json.dumps(false_accepted, ensure_ascii=False)
    assert report["accepted_rate"] >= 0.8, json.dumps(report, ensure_ascii=False)
    for category in {task.category for task in tasks}:
        assert any(
            item["category"] == category
            and item["accepted"]
            and item["oracle_passed"]
            for item in results
        ), json.dumps(report, ensure_ascii=False)
