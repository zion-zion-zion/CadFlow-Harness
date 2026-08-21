from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.trace import _summary_text


def _line(payload: object) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def test_trace_summary_extracts_responses_reasoning_summary() -> None:
    record = {
        "type": "model_response",
        "payload": {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "reasoning",
                            "summary": [
                                {"type": "summary_text", "text": "Checked the CAD constraints."}
                            ],
                        },
                        {"type": "text", "text": "The model is ready."},
                    ],
                }
            ]
        },
    }

    assert _summary_text("model_response", record) == (
        "Reasoning summary: Checked the CAD constraints."
    )


def test_trace_api_lists_projects_reads_incrementally_and_redacts(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Observed run"}).json()
    project_dir = tmp_path / project["project_id"]
    trace_path = project_dir / "conversation.jsonl"
    first = {
        "sequence": 1,
        "timestamp": "2026-08-19T00:00:00.000+00:00",
        "type": "turn_started",
        "conversation_id": project["project_id"],
        "turn_id": "turn-1",
        "payload": {
            "harness": "deepagents",
            "model": {
                "model_id": "cad-model",
                "api_key": "plain-secret",
                "token": "plain-token",
                "token_usage": 42,
            },
        },
    }
    second = {
        "sequence": 2,
        "timestamp": "2026-08-19T00:00:01.000+00:00",
        "type": "tool_call",
        "conversation_id": project["project_id"],
        "turn_id": "turn-1",
        "payload": {
            "tool_name": "read_file",
            "call_id": "tool-1",
            "arguments": {"authorization": "Bearer abcdefghijklmnop", "path": "model.py"},
        },
    }
    incomplete = json.dumps(
        {
            "sequence": 3,
            "timestamp": "2026-08-19T00:00:02.000+00:00",
            "type": "tool_result",
            "conversation_id": project["project_id"],
            "turn_id": "turn-1",
            "payload": {
                "tool_name": "read_file",
                "call_id": "tool-1",
                "result": "ok",
            },
        }
    ).encode("utf-8")
    trace_path.write_bytes(_line(first) + _line(second) + incomplete)

    catalog = client.get("/api/traces")
    assert catalog.status_code == 200
    assert catalog.headers["cache-control"] == "no-store"
    entry = catalog.json()[0]
    assert entry["project_id"] == project["project_id"]
    assert entry["trace_available"] is True
    assert entry["event_count"] == 2

    response = client.get(f"/api/projects/{project['project_id']}/trace")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    batch = response.json()
    assert [event["type"] for event in batch["events"]] == [
        "turn_started",
        "tool_call",
    ]
    assert batch["events"][1]["call_id"] == "tool-1"
    assert batch["has_incomplete_tail"] is True

    detail = client.get(
        f"/api/projects/{project['project_id']}/trace/events",
        params={"cursor": batch["events"][0]["cursor"]},
    )
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["event"]["payload"]["model"]["api_key"] == "[REDACTED]"
    assert detail.json()["event"]["payload"]["model"]["token"] == "[REDACTED]"
    assert detail.json()["event"]["payload"]["model"]["token_usage"] == 42

    tool_detail = client.get(
        f"/api/projects/{project['project_id']}/trace/events",
        params={"cursor": batch["events"][1]["cursor"]},
    ).json()
    assert tool_detail["event"]["payload"]["arguments"]["authorization"] == "[REDACTED]"

    with trace_path.open("ab") as stream:
        stream.write(b"\n")
    incremental = client.get(
        f"/api/projects/{project['project_id']}/trace",
        params={"offset": batch["next_offset"]},
    ).json()
    assert [event["sequence"] for event in incremental["events"]] == [3]
    assert incremental["has_incomplete_tail"] is False


def test_trace_api_searches_full_payload_and_exports_valid_parse_errors(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Searchable run"}).json()
    trace_path = tmp_path / project["project_id"] / "conversation.jsonl"
    trace_path.write_bytes(
        _line(
            {
                "sequence": 1,
                "timestamp": "2026-08-19T00:00:00.000+00:00",
                "type": "model_response",
                "conversation_id": project["project_id"],
                "turn_id": "turn-1",
                "payload": {
                    "messages": [
                        {"role": "assistant", "content": "hidden needle after summary"}
                    ],
                    "password": "do-not-export",
                },
            }
        )
        + b"{broken json}\n"
    )

    found = client.get(
        f"/api/projects/{project['project_id']}/trace",
        params={"q": "needle"},
    ).json()
    assert [event["type"] for event in found["events"]] == ["model_response"]

    all_events = client.get(
        f"/api/projects/{project['project_id']}/trace"
    ).json()["events"]
    assert [event["type"] for event in all_events] == [
        "model_response",
        "parse_error",
    ]
    assert all_events[1]["is_error"] is True

    download = client.get(
        f"/api/projects/{project['project_id']}/trace/download"
    )
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    exported = [json.loads(line) for line in download.text.splitlines()]
    assert exported[0]["payload"]["password"] == "[REDACTED]"
    assert exported[1]["type"] == "parse_error"


def test_trace_api_reports_missing_trace_without_changing_project_api(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Draft"}).json()

    assert "trace_available" not in client.get("/api/projects").json()[0]
    trace_entry = client.get("/api/traces").json()[0]
    assert trace_entry["trace_available"] is False
    assert trace_entry["event_count"] == 0
    assert (
        client.get(f"/api/projects/{project['project_id']}/trace").status_code
        == 404
    )
