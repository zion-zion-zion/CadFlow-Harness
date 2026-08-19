from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from backend.agent_logging import AgentRunLog
from backend.cad_executor import redact_credentials


def test_agent_run_log_persists_prompt_model_and_tool_lifecycle(tmp_path: Path) -> None:
    long_text = "x" * 100_000
    trace = AgentRunLog(
        tmp_path,
    )
    trace.configure(
        provider="openai",
        model_id="cad-model",
        base_url="https://provider.invalid/v1",
    )
    handler = trace.callback_handler()
    handler.on_chat_model_start(
        {"name": "ChatOpenAI", "api_key": "sk-secret-value"},
        [
            [
                SystemMessage(content="system API_KEY=sk-system-secret"),
                HumanMessage(
                    content=f"request API_KEY=sk-secret-value {long_text}",
                    response_metadata={"tokens": 10},
                ),
            ]
        ],
        run_id="model-1",
    )
    handler.on_tool_start(
        {"name": "write_file"},
        '{"file_path":"model.py","content":"print(1)"}',
        run_id="tool-1",
        inputs={"file_path": "model.py", "content": long_text},
    )
    handler.on_tool_end(
        ToolMessage(
            content=f"result OPENAI_API_KEY=sk-another-secret {long_text}",
            tool_call_id="call-1",
            name="write_file",
            response_metadata={"tokens": 10},
        ),
        run_id="tool-1",
    )
    handler.on_llm_end(
        LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content=f"done {long_text}",
                            tool_calls=[
                                {
                                    "name": "write_file",
                                    "args": {"content": long_text},
                                    "id": "call-1",
                                    "type": "tool_call",
                                }
                            ],
                            response_metadata={"model_name": "cad-model"},
                            usage_metadata={
                                "input_tokens": 10,
                                "output_tokens": 20,
                                "total_tokens": 30,
                                "input_token_details": {"cache_read": 4},
                            },
                        )
                    )
                ]
            ],
            llm_output={"model_name": "cad-model", "token_usage": {"total": 30}},
        ),
        run_id="model-1",
    )
    trace.record_internal_tool("validate_model", "model.py")
    trace.finish(
        status="succeeded",
    )

    assert trace.token_usage == {
        "total_tokens": 30,
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 6,
        "output_tokens": 20,
    }

    records = [
        json.loads(line)
        for line in (tmp_path / "agent-run.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    payload = records[0]
    assert payload["model"] == {
        "base_url": "https://provider.invalid/v1",
        "model_id": "cad-model",
        "provider": "openai",
    }
    assert records[-1]["status"] == "succeeded"
    assert set(payload) == {
        "model",
        "sequence",
        "timestamp",
        "type",
    }
    assert [event["type"] for event in records] == [
        "run_started",
        "model_request",
        "tool_call",
        "tool_result",
        "model_response",
        "backend_tool",
        "run_finished",
    ]
    assert records[1]["messages"] == [
        {"role": "system", "content": "system API_KEY=[REDACTED]"},
        {"role": "user", "content": f"request API_KEY=[REDACTED] {long_text}"},
    ]
    assert records[2]["arguments"]["content"] == long_text
    assert records[2]["call_id"] == "tool-1"
    assert records[3]["result"] == f"result OPENAI_API_KEY=[REDACTED] {long_text}"
    assert records[3]["call_id"] == "tool-1"
    response = records[4]["messages"][0]
    assert response["role"] == "assistant"
    assert response["content"] == f"done {long_text}"
    assert response["tool_calls"][0]["args"]["content"] == long_text
    assert "response_metadata" not in json.dumps(records, ensure_ascii=False)
    assert "usage_metadata" not in json.dumps(records, ensure_ascii=False)
    assert "token_usage" not in json.dumps(records, ensure_ascii=False)
    assert "run_id" not in json.dumps(records, ensure_ascii=False)
    assert "provider_retry_count" not in records[-1]
    serialized = json.dumps(records, ensure_ascii=False)
    assert "sk-secret-value" not in serialized
    assert "sk-system-secret" not in serialized
    assert "sk-another-secret" not in serialized


def test_agent_run_log_accumulates_cached_and_uncached_token_usage(
    tmp_path: Path,
) -> None:
    trace = AgentRunLog(tmp_path)
    handler = trace.callback_handler()

    handler.on_llm_end(
        {
            "generations": [
                [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "first",
                            "usage_metadata": {
                                "input_tokens": 10,
                                "output_tokens": 20,
                                "total_tokens": 30,
                                "input_token_details": {"cache_read": 4},
                            },
                        }
                    }
                ]
            ]
        },
        run_id="model-1",
    )
    handler.on_llm_end(
        {
            "generations": [
                [{"message": {"role": "assistant", "content": "second"}}]
            ],
            "llm_output": {
                "token_usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "total_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 2},
                }
            },
        },
        run_id="model-2",
    )

    assert trace.token_usage == {
        "total_tokens": 40,
        "input_tokens": 14,
        "cached_input_tokens": 6,
        "uncached_input_tokens": 8,
        "output_tokens": 26,
    }

    handler.on_llm_end(
        {
            "generations": [
                [{"message": {"role": "assistant", "content": "third"}}]
            ],
            "llm_output": {"token_usage": {"total": 5}},
        },
        run_id="model-3",
    )

    assert trace.token_usage == {
        "total_tokens": 40,
        "input_tokens": 14,
        "cached_input_tokens": 6,
        "uncached_input_tokens": 8,
        "output_tokens": 26,
    }
    assert "token_usage" not in (tmp_path / "agent-run.jsonl").read_text(
        encoding="utf-8"
    )


def test_agent_run_log_counts_missing_cache_details_as_uncached(
    tmp_path: Path,
) -> None:
    trace = AgentRunLog(tmp_path)
    handler = trace.callback_handler()

    handler.on_llm_end(
        {
            "generations": [[{"message": {"role": "assistant", "content": "ok"}}]],
            "llm_output": {
                "token_usage": {
                    "prompt_tokens": 25,
                    "completion_tokens": 5,
                }
            },
        },
        run_id="model-1",
    )

    assert trace.token_usage == {
        "total_tokens": 30,
        "input_tokens": 25,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 25,
        "output_tokens": 5,
    }


def test_agent_run_log_caps_cached_tokens_at_input_tokens(tmp_path: Path) -> None:
    trace = AgentRunLog(tmp_path)

    trace.record_model_usage(
        {
            "usage_metadata": {
                "input_tokens": 10,
                "output_tokens": 2,
                "input_token_details": {"cache_read": 20},
            }
        }
    )

    assert trace.token_usage == {
        "total_tokens": 12,
        "input_tokens": 10,
        "cached_input_tokens": 10,
        "uncached_input_tokens": 0,
        "output_tokens": 2,
    }


def test_agent_run_log_survives_malformed_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "agent-run.jsonl"
    path.write_text("not-json", encoding="utf-8")

    trace = AgentRunLog(tmp_path)

    assert trace.data["records"][0]["type"] == "run_started"
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["type"] == "run_started"


def test_redaction_helper_remains_compatible_with_agent_trace() -> None:
    assert redact_credentials("Bearer abcdefghijklmnop") == "Bearer [REDACTED]"
