from __future__ import annotations

import importlib
from pathlib import Path


def test_agent_is_a_split_package_with_stable_public_imports() -> None:
    agent = importlib.import_module("backend.agent")
    backend = importlib.import_module("backend")

    assert agent.__path__
    assert Path(__file__).parents[1].joinpath("backend", "agent.py").exists() is False

    public_names = (
        "AgentSettings",
        "AgentRunOutcome",
        "AgentRunService",
        "ReferenceGroundedAgent",
        "build_chat_model",
        "build_deep_agent",
        "create_agent_tools",
    )
    for name in public_names:
        assert getattr(agent, name) is not None
        assert getattr(backend, name) is getattr(agent, name)

    assert getattr(agent, "DEEPAGENTS_IMPLEMENTATION_VERSION")
    assert backend.DEEPAGENTS_IMPLEMENTATION_VERSION is agent.DEEPAGENTS_IMPLEMENTATION_VERSION


def test_agent_split_modules_import_without_cycles() -> None:
    for module_name in (
        "backend.agent.settings",
        "backend.agent.outcome",
        "backend.agent.prompt",
        "backend.agent.tools",
        "backend.agent.runtime",
        "backend.agent.service",
    ):
        module = importlib.import_module(module_name)
        assert module.__name__ == module_name
