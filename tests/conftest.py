from __future__ import annotations

import os

import pytest

from backend.agent import AgentConfigurationError, AgentSettings


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Require an explicit, credentialed opt-in before a live Agent test."""

    if item.get_closest_marker("live_agent") is None:
        return
    if os.environ.get("TEXT_TO_CAD_RUN_LIVE_AGENT") != "1":
        pytest.skip(
            "live Agent tests require TEXT_TO_CAD_RUN_LIVE_AGENT=1; "
            "no model call was made"
        )
    try:
        AgentSettings.from_environment()
    except AgentConfigurationError as exc:
        pytest.skip(f"live Agent provider is not configured: {exc}")
