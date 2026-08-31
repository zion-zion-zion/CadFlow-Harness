"""Single-process Agent Run ownership, cancellation, and recovery."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .agent import (
    AgentRunError,
    AgentRunOutcome,
    AgentRunService,
    DEEPAGENTS_IMPLEMENTATION_VERSION,
    ReferenceGroundedAgent,
)
from .agent_logging import ConversationLog
from .cad_executor import CancellationToken, redact_credentials
from .events import ProgressEventStore, ProgressUpdate
from .harnesses import (
    AgentHarness,
    AgentRunAdapter,
    AgentRunAdapterRegistry,
)
from .live_preview import LivePreviewScheduler, LivePreviewStatus
from .projects import (
    Project,
    ProjectState,
    ProjectStateError,
    ProjectStore,
)


RUN_STOP_WAIT_SECONDS = 5.0


class RunConflictError(ProjectStateError):
    """Raised when another Project already owns the global Agent Run slot."""


@dataclass
class _ActiveRun:
    project_id: str
    prompt: str
    turn_id: str
    request_id: str
    conversation_log: ConversationLog
    cancellation_token: CancellationToken
    finished: threading.Event
    adapter: AgentRunAdapter
    thread: threading.Thread | None = None
    stopping: bool = False
    finalized: bool = False


@dataclass(frozen=True)
class MessageSubmission:
    project: Project
    turn_id: str
    request_id: str
    duplicate: bool = False


class AgentRunCoordinator:
    """Own the process-local global run lock and active task handle."""

    def __init__(
        self,
        *,
        store: ProjectStore,
        repo_root: str | Path,
        event_store: ProgressEventStore | None = None,
        run_service: Any | None = None,
        adapter_registry: AgentRunAdapterRegistry | None = None,
        settings_factory: Callable[[], Any] | None = None,
        agent_factory: Callable[..., Any] | None = None,
        preview_scheduler: LivePreviewScheduler | None = None,
    ) -> None:
        self.store = store
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.events = event_store or ProgressEventStore(store.root)
        self.preview_scheduler = preview_scheduler or LivePreviewScheduler(
            store, on_status=self._record_preview_status
        )
        self.run_service = run_service or AgentRunService(
            store=store,
            repo_root=self.repo_root,
            settings_factory=settings_factory,
            agent_factory=agent_factory or ReferenceGroundedAgent,
        )
        self.adapters = adapter_registry or AgentRunAdapterRegistry(
            (
                AgentRunAdapter(
                    AgentHarness.DEEPAGENTS,
                    self.run_service,
                    DEEPAGENTS_IMPLEMENTATION_VERSION,
                ),
            )
        )
        self._lock = threading.RLock()
        self._active: _ActiveRun | None = None

    @property
    def active_project_id(self) -> str | None:
        with self._lock:
            return self._active.project_id if self._active is not None else None

    @property
    def active_process_id(self) -> int | None:
        with self._lock:
            return (
                self._active.cancellation_token.active_process_id
                if self._active is not None
                else None
            )

    def recover_interrupted_runs(self) -> tuple[Project, ...]:
        """Reconcile disk state without pretending to recover Agent checkpoints."""

        recovered = self.store.recover_interrupted_runs()
        for project in recovered:
            conversation = self.store.conversation_log(project.project_id)
            running_turn = next(
                (
                    turn
                    for turn in reversed(conversation.turns())
                    if turn["status"] == "running"
                ),
                None,
            )
            if running_turn is not None:
                self.store.conversation_log(
                    project.project_id,
                    turn_id=str(running_turn["turn_id"]),
                ).finish(
                    status="failed",
                    failure_reason=project.failure_reason,
                    assistant_message=project.failure_reason,
                )
            self.events.append(
                project.project_id,
                stage="failed",
                tool="service",
                result=project.failure_reason or "Agent Run interrupted by restart",
            )
        return recovered

    def start(
        self,
        project_id: str,
        prompt: str,
        harness: AgentHarness | str = AgentHarness.DEEPAGENTS,
    ) -> Project:
        return self.start_message(
            project_id,
            prompt,
            request_id=uuid.uuid4().hex,
            harness=harness,
        ).project

    def start_message(
        self,
        project_id: str,
        prompt: str,
        *,
        request_id: str,
        retry_of: str | None = None,
        harness: AgentHarness | str = AgentHarness.DEEPAGENTS,
    ) -> MessageSubmission:
        adapter = self.adapters.get(harness)
        adapter.require_available()
        with self._lock:
            conversation = self.store.conversation_log(project_id)
            existing = conversation.turn_for_request(request_id)
            if existing is not None:
                return MessageSubmission(
                    project=self.store.get_project(project_id),
                    turn_id=str(existing["turn_id"]),
                    request_id=request_id,
                    duplicate=True,
                )
            if self._active is not None:
                raise RunConflictError("another Agent Run is already active")
            if retry_of is not None and conversation.turn(retry_of) is None:
                raise ProjectStateError("retry_of does not identify a Project turn")
            project = self.store.submit_prompt(project_id, prompt, adapter.harness)
            turn_id = uuid.uuid4().hex
            conversation_log = self.store.conversation_log(
                project.project_id,
                turn_id=turn_id,
                request_id=request_id,
                user_message=prompt,
                retry_of=retry_of,
                harness=adapter.harness.value,
                implementation_version=adapter.implementation_version,
            )
            token = CancellationToken()
            active = _ActiveRun(
                project_id=project.project_id,
                prompt=project.prompt or prompt,
                turn_id=turn_id,
                request_id=request_id,
                conversation_log=conversation_log,
                cancellation_token=token,
                finished=threading.Event(),
                adapter=adapter,
            )
            self._active = active
            self.events.append(
                project.project_id,
                stage="preparing",
                tool="service",
                result=f"{adapter.label} Agent Run accepted",
            )
            self._preview_call("activate", project.project_id)
            thread = threading.Thread(
                target=self._run_worker,
                args=(active,),
                name=f"text-to-cad-run-{project.project_id[:8]}",
                daemon=True,
            )
            active.thread = thread
            thread.start()
            return MessageSubmission(
                project=project,
                turn_id=turn_id,
                request_id=request_id,
            )

    def wait_for_turn(
        self,
        turn_id: str,
        timeout_seconds: float | None = None,
    ) -> bool:
        with self._lock:
            active = self._active
            if active is None or active.turn_id != turn_id:
                return True
        return active.finished.wait(timeout_seconds)

    def stop(self, project_id: str) -> Project:
        with self._lock:
            project = self.store.get_project(project_id)
            if project.state != ProjectState.RUNNING:
                raise ProjectStateError("Stop is valid only for Running Projects")
            active = self._active
            if active is None or active.project_id != project_id:
                raise ProjectStateError("Running Project has no active Agent Run")
            if not active.stopping:
                active.stopping = True
                self.events.append(
                    project_id,
                    stage="stopping",
                    tool="service",
                    result="Stop requested by user",
                )
                active.cancellation_token.cancel()
                self._preview_call("deactivate", project_id)
        if not active.finished.wait(RUN_STOP_WAIT_SECONDS):
            force_stop = getattr(active.adapter.service, "force_stop", None)
            if callable(force_stop):
                force_stop(project_id)
                active.finished.wait(1.0)
        return self.store.get_project(project_id)

    def delete(self, project_id: str) -> None:
        """Cancel a Running Project before permanently removing its directory."""

        with self._lock:
            project = self.store.get_project(project_id)
            active = self._active
            if active is not None and active.project_id == project_id:
                if not active.stopping:
                    active.stopping = True
                    self.events.append(
                        project_id,
                        stage="stopping",
                        tool="service",
                        result="Deletion requested; cancelling Agent Run",
                    )
                    active.cancellation_token.cancel()
                    self._preview_call("deactivate", project_id)
            elif project.state == ProjectState.RUNNING:
                raise ProjectStateError("Running Project has no active Agent Run")

        if active is not None and active.project_id == project_id:
            if not active.finished.wait(RUN_STOP_WAIT_SECONDS):
                force_stop = getattr(active.adapter.service, "force_stop", None)
                if callable(force_stop):
                    force_stop(project_id)
                    active.finished.wait(1.0)
            if not active.finished.is_set():
                raise ProjectStateError(
                    "Agent Run did not stop before Project deletion"
                )

        with self._lock:
            if self._active is not None and self._active.project_id == project_id:
                raise ProjectStateError("Agent Run is still active")
            self.store.delete_project(project_id)

    def wait_for_idle(self, timeout_seconds: float = RUN_STOP_WAIT_SECONDS) -> bool:
        with self._lock:
            active = self._active
        if active is None:
            return True
        return active.finished.wait(timeout_seconds)

    def _run_worker(self, active: _ActiveRun) -> None:
        outcome: AgentRunOutcome
        try:
            outcome = self._invoke_service(active)
        except Exception as exc:
            outcome = AgentRunOutcome(
                validated=False,
                failure_reason=_safe_reason(exc),
            )
        try:
            self._finish_project(active, outcome)
        finally:
            active.finished.set()
            with self._lock:
                if self._active is active:
                    self._active = None

    def _invoke_service(self, active: _ActiveRun) -> AgentRunOutcome:
        result = active.adapter.service.run(
            active.project_id,
            active.prompt,
            cancellation_token=active.cancellation_token,
            progress_callback=self._progress_callback(active.project_id),
            conversation_log=active.conversation_log,
        )
        if not isinstance(result, AgentRunOutcome):
            raise AgentRunError("Agent Run service returned an invalid outcome")
        return replace(
            result,
            harness=active.adapter.harness,
            implementation_version=active.adapter.implementation_version,
        )

    def _progress_callback(self, project_id: str) -> Callable[[ProgressUpdate], None]:
        def record(update: ProgressUpdate) -> None:
            self.events.append(
                project_id,
                stage=update.stage,
                tool=update.tool,
                attempt=update.attempt,
                result=update.result,
                preview_attempt=update.preview_attempt,
                preview_revision=update.preview_revision,
                preview_operation=update.preview_operation,
            )

        return record

    def _record_preview_status(
        self, project_id: str, status: LivePreviewStatus
    ) -> None:
        result = status.error or {
            "waiting": "Waiting for source changes",
            "stale": "Source changed",
            "building": "Building live preview",
            "current": "Live preview is current",
            "paused": "Live preview paused",
            "failed": "Live preview failed",
        }.get(status.state, status.state)
        preview_ready = status.state == "current" and status.revision > 0
        self.events.append(
            project_id,
            stage=f"preview_{status.state}",
            tool="preview",
            result=result,
            preview_attempt=1 if preview_ready else None,
            preview_revision=status.revision if preview_ready else None,
            preview_operation="result" if preview_ready else None,
        )

    def _preview_call(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        try:
            method = getattr(self.preview_scheduler, method_name)
            method(*args, **kwargs)
        except Exception:
            # Live preview is user-facing observability and cannot affect a run.
            return

    def _finish_project(self, active: _ActiveRun, outcome: AgentRunOutcome) -> None:
        # Serialize the terminal decision with Stop so a caller cancellation
        # cannot be followed by a successful Artifact promotion.
        with self._lock:
            if active.finalized:
                return
            active.finalized = True
            project = self.store.get_project(active.project_id)
            if project.state == ProjectState.RUNNING:
                stop_requested = (
                    active.stopping
                    or active.cancellation_token.cancellation_reason == "caller"
                    or outcome.cancelled
                )
                try:
                    if stop_requested:
                        project = self.store.mark_stopped(
                            active.project_id,
                            outcome.failure_reason or "Agent Run stopped by user",
                            outcome.diagnostics(),
                        )
                    elif outcome.validated:
                        try:
                            project = self.store.mark_succeeded(
                                active.project_id, outcome.diagnostics()
                            )
                        except Exception as exc:
                            # Artifact promotion is part of the terminal
                            # transition. A promotion error turns the Run
                            # into Failed and never leaves a false success.
                            reason = _safe_reason(exc)
                            outcome = replace(
                                outcome,
                                validated=False,
                                failure_reason=reason,
                            )
                            project = self.store.mark_failed(
                                active.project_id,
                                reason,
                                outcome.diagnostics(),
                            )
                    else:
                        project = self.store.mark_failed(
                            active.project_id,
                            outcome.failure_reason or "Agent Run failed",
                            outcome.diagnostics(),
                        )
                except ProjectStateError as exc:
                    project = self.store.get_project(active.project_id)
                    if project.state == ProjectState.RUNNING:
                        try:
                            project = self.store.mark_failed(
                                active.project_id,
                                _safe_reason(exc),
                                outcome.diagnostics(),
                            )
                        except ProjectStateError:
                            project = self.store.get_project(active.project_id)

            if project.state == ProjectState.SUCCEEDED:
                final_stage = "completed"
                final_result = "Validated Result is ready"
            elif project.state == ProjectState.STOPPED:
                final_stage = "stopped"
                final_result = "Agent Run stopped; unvalidated Scene Artifact discarded"
            elif project.state == ProjectState.FAILED:
                final_stage = "failed"
                final_result = project.failure_reason or "Agent Run failed"
            else:
                # This is only reachable if persistence itself failed before a
                # terminal state could be written. Keep the turn consistent.
                final_stage = "failed"
                final_result = "Agent Run did not reach a terminal Project state"

            self._preview_call(
                "deactivate",
                active.project_id,
                validated=project.state == ProjectState.SUCCEEDED,
            )
            self.events.append(
                active.project_id,
                stage=final_stage,
                tool="service",
                result=final_result,
            )
            succeeded = project.state == ProjectState.SUCCEEDED
            assistant_message = active.conversation_log.latest_model_response_text()
            if not assistant_message:
                if succeeded:
                    version = self.store.current_artifact_version(active.project_id)
                    assistant_message = (
                        f"CAD model updated successfully. Artifact v{version:04d} is ready."
                        if version is not None
                        else "CAD model updated successfully."
                    )
                else:
                    assistant_message = project.failure_reason or "The CAD turn failed."
            active.conversation_log.finish(
                status="succeeded" if succeeded else project.state.value.lower(),
                failure_reason=None if succeeded else project.failure_reason,
                assistant_message=assistant_message,
                artifact_version=(
                    self.store.current_artifact_version(active.project_id)
                    if succeeded
                    else None
                ),
            )


def _safe_reason(error: Exception) -> str:
    first = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return redact_credentials(first)[:500] or type(error).__name__


__all__ = [
    "AgentRunCoordinator",
    "MessageSubmission",
    "RUN_STOP_WAIT_SECONDS",
    "RunConflictError",
]
