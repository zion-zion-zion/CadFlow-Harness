"""Configuration and model construction for the primary CAD Agent."""

from __future__ import annotations

import importlib.metadata
import math
import os
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast


class AgentConfigurationError(RuntimeError):
    """Raised when the backend model configuration is incomplete."""


AGENT_RUN_TIMEOUT_ENV_VAR = "CADFLOW_AGENT_RUN_TIMEOUT_SECONDS"
REVIEW_MODEL_ENV_VAR = "OPENAI_REVIEW_MODEL_ID"
DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 20 * 60.0
# Backward-compatible names for callers that need the default budget. Runtime
# runs use AgentSettings.run_timeout_seconds so repository-local .env values
# are resolved after dotenv loading.
MAX_AGENT_RUN_SECONDS = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
AGENT_RUN_TIMEOUT_SECONDS = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
MAX_PROVIDER_RETRIES = 2

try:
    DEEPAGENTS_IMPLEMENTATION_VERSION = importlib.metadata.version("deepagents")
except importlib.metadata.PackageNotFoundError:
    DEEPAGENTS_IMPLEMENTATION_VERSION = "unknown"

AGENT_EXCLUDED_TOOLS = frozenset({"execute", "task"})
AGENT_FILESYSTEM_TOOLS = (
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "ls",
)

ReasoningEffort = Literal["none", "low", "medium", "high", "max"]
ReasoningSummary = Literal["auto", "concise", "detailed"]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "low",
    "medium",
    "high",
    "max",
)
REASONING_SUMMARIES: tuple[ReasoningSummary, ...] = (
    "auto",
    "concise",
    "detailed",
)


def resolve_agent_run_timeout_seconds(
    environment: Mapping[str, str] | None = None,
) -> float:
    """Resolve the positive per-run wall-clock budget from the environment."""

    values = os.environ if environment is None else environment
    raw = values.get(AGENT_RUN_TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    try:
        timeout_seconds = float(raw)
    except ValueError as error:
        raise AgentConfigurationError(
            f"{AGENT_RUN_TIMEOUT_ENV_VAR} must be a finite positive number of seconds"
        ) from error
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise AgentConfigurationError(
            f"{AGENT_RUN_TIMEOUT_ENV_VAR} must be a finite positive number of seconds"
        )
    return timeout_seconds


def _format_timeout_seconds(timeout_seconds: float) -> str:
    return f"{timeout_seconds:g}"


def _agent_run_timeout_message(timeout_seconds: float) -> str:
    return (
        "Agent Run exceeded the configured "
        f"{_format_timeout_seconds(timeout_seconds)}-second wall-clock limit"
    )


@dataclass(frozen=True)
class AgentSettings:
    """Backend-only configuration for the Agent and optional CAD reviewer."""

    model_id: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    provider: str = "openai"
    reasoning_effort: ReasoningEffort | None = None
    reasoning_summary: ReasoningSummary | None = None
    run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    review_model_id: str | None = None

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "AgentSettings":
        values = os.environ if environment is None else environment
        missing = [
            name
            for name in ("OPENAI_API_KEY", "OPENAI_MODEL_ID")
            if not values.get(name, "").strip()
        ]
        if missing:
            raise AgentConfigurationError(
                "missing required Agent configuration: " + ", ".join(missing)
            )
        base_url = values.get("OPENAI_BASE_URL", "").strip() or None
        reasoning_effort = values.get("OPENAI_REASONING_EFFORT", "").strip()
        if reasoning_effort and reasoning_effort not in REASONING_EFFORTS:
            allowed = ", ".join(REASONING_EFFORTS)
            raise AgentConfigurationError(
                f"OPENAI_REASONING_EFFORT must be one of: {allowed}"
            )
        reasoning_summary = values.get("OPENAI_REASONING_SUMMARY", "").strip()
        if reasoning_summary and reasoning_summary not in REASONING_SUMMARIES:
            allowed = ", ".join(REASONING_SUMMARIES)
            raise AgentConfigurationError(
                f"OPENAI_REASONING_SUMMARY must be one of: {allowed}"
            )
        return cls(
            model_id=values["OPENAI_MODEL_ID"].strip(),
            api_key=values["OPENAI_API_KEY"],
            base_url=base_url,
            reasoning_effort=(
                cast(ReasoningEffort, reasoning_effort)
                if reasoning_effort
                else None
            ),
            reasoning_summary=(
                cast(ReasoningSummary, reasoning_summary)
                if reasoning_summary
                else None
            ),
            run_timeout_seconds=resolve_agent_run_timeout_seconds(values),
            review_model_id=values.get(REVIEW_MODEL_ENV_VAR, "").strip() or None,
        )

    @property
    def deep_agent_model_spec(self) -> str:
        return f"{self.provider}:{self.model_id}"

    @property
    def use_responses_api(self) -> bool:
        """Use Chat Completions only when reasoning is explicitly disabled."""

        return self.reasoning_effort != "none"

    @property
    def reasoning_parameters(self) -> dict[str, str] | None:
        """Return Responses-only reasoning options, when Responses is active."""

        if not self.use_responses_api:
            return None
        parameters: dict[str, str] = {}
        if self.reasoning_effort is not None:
            parameters["effort"] = self.reasoning_effort
        if self.reasoning_summary is not None:
            parameters["summary"] = self.reasoning_summary
        return parameters or None


def build_chat_model(settings: AgentSettings) -> Any:
    """Construct the configured LangChain model without exposing its key."""

    from langchain_openai import ChatOpenAI

    arguments: dict[str, Any] = {
        "model": settings.model_id,
        "api_key": settings.api_key,
        "max_retries": MAX_PROVIDER_RETRIES,
        "timeout": settings.run_timeout_seconds,
        "use_responses_api": settings.use_responses_api,
    }
    if settings.base_url is not None:
        arguments["base_url"] = settings.base_url
    if settings.reasoning_parameters is not None:
        arguments["reasoning"] = settings.reasoning_parameters
    elif settings.reasoning_effort is not None:
        arguments["reasoning_effort"] = settings.reasoning_effort
    return ChatOpenAI(**arguments)


__all__ = [
    "AgentConfigurationError",
    "AgentSettings",
    "ReasoningEffort",
    "ReasoningSummary",
    "AGENT_RUN_TIMEOUT_ENV_VAR",
    "REVIEW_MODEL_ENV_VAR",
    "AGENT_RUN_TIMEOUT_SECONDS",
    "DEFAULT_AGENT_RUN_TIMEOUT_SECONDS",
    "MAX_AGENT_RUN_SECONDS",
    "MAX_PROVIDER_RETRIES",
    "DEEPAGENTS_IMPLEMENTATION_VERSION",
    "AGENT_EXCLUDED_TOOLS",
    "AGENT_FILESYSTEM_TOOLS",
    "resolve_agent_run_timeout_seconds",
    "build_chat_model",
]
