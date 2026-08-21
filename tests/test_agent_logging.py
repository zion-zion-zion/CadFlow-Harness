from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from backend.agent_logging import ConversationLog
from backend.cad_executor import redact_credentials


def test_conversation_log_persists_turn_model_and_tool_lifecycle(tmp_path: Path) -> None:
    long_text = "x" * 100_000
    trace = ConversationLog(
        tmp_path,
        conversation_id="project-1",
        turn_id="turn-1",
        request_id="request-1",
    )
    trace.configure(
        provider="openai",
        model_id="cad-model",
        base_url="https://provider.invalid/v1",
        reasoning_effort="high",
        reasoning_summary="auto",
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
        for line in (tmp_path / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    payload = records[0]
    assert payload["payload"]["model"] == {
        "base_url": "https://provider.invalid/v1",
        "model_id": "cad-model",
        "provider": "openai",
        "reasoning_effort": "high",
        "reasoning_summary": "auto",
    }
    assert records[-1]["payload"]["status"] == "succeeded"
    assert set(payload) == {
        "conversation_id",
        "payload",
        "sequence",
        "timestamp",
        "turn_id",
        "type",
    }
    assert [event["type"] for event in records] == [
        "turn_started",
        "model_request",
        "tool_call",
        "tool_result",
        "model_response",
        "backend_tool",
        "turn_succeeded",
    ]
    assert records[1]["payload"]["messages"] == [
        {"role": "system", "content": "system API_KEY=[REDACTED]"},
        {"role": "user", "content": f"request API_KEY=[REDACTED] {long_text}"},
    ]
    external_arguments = records[2]["payload"]["arguments"]
    assert external_arguments["characters"] > 100_000
    assert (tmp_path / external_arguments["external_path"]).is_file()
    assert records[2]["payload"]["call_id"] == "tool-1"
    external_result = records[3]["payload"]["result"]
    assert external_result["characters"] > 100_000
    assert (tmp_path / external_result["external_path"]).is_file()
    assert records[3]["payload"]["call_id"] == "tool-1"
    response = records[4]["payload"]["messages"][0]
    assert response["role"] == "assistant"
    assert response["content"] == f"done {long_text}"
    assert response["tool_calls"][0]["args"]["content"] == long_text
    assert "response_metadata" not in json.dumps(records, ensure_ascii=False)
    assert "usage_metadata" not in json.dumps(records, ensure_ascii=False)
    assert "token_usage" not in json.dumps(records, ensure_ascii=False)
    assert "run_id" not in json.dumps(records, ensure_ascii=False)
    assert "provider_retry_count" not in records[-1]["payload"]
    serialized = json.dumps(records, ensure_ascii=False)
    assert "sk-secret-value" not in serialized
    assert "sk-system-secret" not in serialized
    assert "sk-another-secret" not in serialized


def test_conversation_log_accumulates_cached_and_uncached_token_usage(
    tmp_path: Path,
) -> None:
    trace = ConversationLog(tmp_path, turn_id="turn-1")
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
    assert "token_usage" not in (tmp_path / "conversation.jsonl").read_text(
        encoding="utf-8"
    )


def test_conversation_log_counts_missing_cache_details_as_uncached(
    tmp_path: Path,
) -> None:
    trace = ConversationLog(tmp_path, turn_id="turn-1")
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


def test_conversation_log_caps_cached_tokens_at_input_tokens(tmp_path: Path) -> None:
    trace = ConversationLog(tmp_path, turn_id="turn-1")

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


def test_conversation_log_recovers_an_incomplete_tail(tmp_path: Path) -> None:
    path = tmp_path / "conversation.jsonl"
    path.write_text("not-json", encoding="utf-8")

    trace = ConversationLog(tmp_path, turn_id="turn-1")

    assert trace.data["records"][0]["type"] == "turn_started"
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["type"] == "turn_started"


def test_conversation_log_replaces_the_legacy_log_after_opening(tmp_path: Path) -> None:
    legacy = tmp_path / "agent-run.jsonl"
    legacy.write_text('{"type":"run_started"}\n', encoding="utf-8")

    log = ConversationLog(tmp_path, turn_id="turn-1", user_message="Create a box.")

    assert log.path.name == "conversation.jsonl"
    assert log.path.is_file()
    assert not legacy.exists()


def test_conversation_context_replays_turns_and_summarizes_over_the_limit(
    tmp_path: Path,
) -> None:
    first = ConversationLog(
        tmp_path,
        turn_id="turn-1",
        request_id="request-1",
        user_message="Create a wide mounting plate.",
    )
    first.finish(status="succeeded", assistant_message="The plate is ready.")
    second = ConversationLog(
        tmp_path,
        turn_id="turn-2",
        request_id="request-2",
        user_message="Add four corner holes.",
    )

    assert second.context_messages(exclude_turn_id="turn-2") == [
        {"role": "user", "content": "Create a wide mounting plate."},
        {"role": "assistant", "content": "The plate is ready."},
    ]
    summarized = second.context_messages(
        exclude_turn_id="turn-2",
        max_chars=20,
        recent_turns=0,
    )
    assert summarized[0]["role"] == "system"
    assert any(
        record["type"] == "context_summary" for record in second.data["records"]
    )


def test_redaction_helper_remains_compatible_with_agent_trace() -> None:
    assert redact_credentials("Bearer abcdefghijklmnop") == "Bearer [REDACTED]"
