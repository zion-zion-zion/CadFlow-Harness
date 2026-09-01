"""Environment filtering and bounded credential redaction for CAD boundaries."""

from __future__ import annotations

import os
import re
from typing import Mapping


_SENSITIVE_ENV_NAME = re.compile(
    r"(?i)(api[_-]?(?:key|token)|access[_-]?token|secret|password|credential|"
    r"authorization|endpoint|base[_-]?url|openai|anthropic|gemini|langchain|"
    r"langsmith|cohere|mistral|groq|azure)"
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b([A-Za-z][A-Za-z0-9_.-]*(?:api[_-]?(?:key|token)|access[_-]?token|"
    r"secret|password|credential|authorization|endpoint|base[_-]?url))"
    r"\s*([=:])\s*([^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_SECRET = re.compile(
    r"\b(?:sk|pk|ghp|github_pat|xox[baprs])-[A-Za-z0-9._~+/=-]{8,}"
)


def build_cad_environment(
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy runtime environment while removing model-provider credentials."""

    environment = dict(os.environ if base_environment is None else base_environment)
    for name in tuple(environment):
        if _SENSITIVE_ENV_NAME.search(name):
            environment.pop(name, None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def redact_credentials(text: str) -> str:
    """Redact common key/token/endpoint forms from bounded diagnostics."""

    redacted = _ASSIGNMENT_SECRET.sub(r"\1\2[REDACTED]", text)
    redacted = _BEARER_SECRET.sub(r"\1 [REDACTED]", redacted)
    return _TOKEN_SECRET.sub("[REDACTED]", redacted)


def safe_output(
    raw: bytes,
    limit: int,
    *,
    already_truncated: bool,
) -> tuple[str, bool]:
    """Decode, redact, and apply the final byte bound to process output."""

    redacted = redact_credentials(raw.decode("utf-8", errors="replace"))
    encoded = redacted.encode("utf-8")
    truncated = already_truncated or len(encoded) > limit
    if len(encoded) > limit:
        encoded = encoded[:limit]
        redacted = encoded.decode("utf-8", errors="ignore")
    return redacted, truncated


__all__ = ["build_cad_environment", "redact_credentials", "safe_output"]
