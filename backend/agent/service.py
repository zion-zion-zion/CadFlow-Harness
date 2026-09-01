"""Coordinator-facing Agent Run service."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..agent_logging import ConversationLog
from ..cad_process import CancellationToken
from ..events import ProgressUpdate
from ..projects import ProjectStore
from .outcome import AgentRunError, AgentRunOutcome
from .runtime import ReferenceGroundedAgent
from .settings import AgentSettings
from .tools import _safe_failure_reason


class AgentRunService:
    """Execute Agent work for a coordinator-owned Project turn.

    The coordinator owns Prompt submission, Conversation turns, terminal
    Project state, and Artifact promotion. This service only loads execution
    context and returns a structured outcome.
    """

    def __init__(
        self,
        *,
        store: ProjectStore,
        repo_root: str | Path,
        settings_factory: Callable[[], AgentSettings] | None = None,
        agent_factory: Callable[..., ReferenceGroundedAgent] = ReferenceGroundedAgent,
    ) -> None:
        self.store = store
        self.repo_root = repo_root
        self.settings_factory = settings_factory or AgentSettings.from_environment
        self.agent_factory = agent_factory

    def run(
        self,
        project_id: str,
        prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: Callable[[ProgressUpdate], None],
        conversation_log: ConversationLog,
    ) -> AgentRunOutcome:
        project = self.store.get_project(project_id)
        token = cancellation_token
        log = conversation_log
        if token.cancelled:
            return AgentRunOutcome(
                validated=False,
                cancelled=True,
                failure_reason="Agent Run stopped by caller",
            )
        try:
            settings = self.settings_factory()
            log.configure(
                provider=settings.provider,
                model_id=settings.model_id,
                base_url=settings.base_url,
                reasoning_effort=settings.reasoning_effort,
                reasoning_summary=settings.reasoning_summary,
            )
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
            if token.cancelled:
                outcome = replace(
                    outcome,
                    cancelled=True,
                    failure_reason="Agent Run stopped by caller",
                )
            return outcome

        try:
            factory_kwargs: dict[str, Any] = {
                "settings": settings,
                "repo_root": self.repo_root,
                "project_dir": self.store.project_directory(project.project_id),
                "conversation_log": log,
            }
            agent = self.agent_factory(**factory_kwargs)
            conversation_context = log.context_messages(
                exclude_turn_id=log.turn_id
            )
            outcome = agent.run(
                project.prompt or prompt,
                cancellation_token=token,
                progress_callback=progress_callback,
                conversation_context=conversation_context,
            )
            if not isinstance(outcome, AgentRunOutcome):
                raise AgentRunError("Agent returned an invalid Run outcome")
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
        if log.token_usage is not None:
            outcome = replace(outcome, token_usage=log.token_usage)
        if (
            token.cancelled
            and token.cancellation_reason != "timeout"
            and not outcome.cancelled
        ):
            outcome = replace(
                outcome,
                validated=False,
                cancelled=True,
                failure_reason="Agent Run stopped by caller",
            )
        return outcome


__all__ = ["AgentRunService"]
