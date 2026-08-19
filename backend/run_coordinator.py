"""Single-process Agent Run ownership, cancellation, and recovery."""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .agent import (
    AgentRunError,
    AgentRunOutcome,
    AgentRunService,
    DEEPAGENTS_IMPLEMENTATION_VERSION,
    ReferenceGroundedAgent,
)
from .cad_executor import CancellationToken, redact_credentials
from .events import ProgressEventStore, ProgressUpdate
from .harnesses import (
    AgentHarness,
    AgentRunAdapter,
    AgentRunAdapterRegistry,
)
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
    cancellation_token: CancellationToken
    finished: threading.Event
    adapter: AgentRunAdapter
    thread: threading.Thread | None = None
    stopping: bool = False


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
    ) -> None:
        self.store = store
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.events = event_store or ProgressEventStore(store.root)
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
        adapter = self.adapters.get(harness)
        adapter.require_available()
        with self._lock:
            if self._active is not None:
                raise RunConflictError("another Agent Run is already active")
            project = self.store.submit_prompt(project_id, prompt, adapter.harness)
            token = CancellationToken()
            active = _ActiveRun(
                project_id=project.project_id,
                prompt=project.prompt or prompt,
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
            thread = threading.Thread(
                target=self._run_worker,
                args=(active,),
                name=f"text-to-cad-run-{project.project_id[:8]}",
                daemon=True,
            )
            active.thread = thread
            thread.start()
            return project

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
        run: Any = active.adapter.service.run
        kwargs: dict[str, object] = {}
        parameters: Mapping[str, inspect.Parameter]
        try:
            parameters = inspect.signature(run).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_kwargs = any(
            item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
        )
        if accepts_kwargs or "cancellation_token" in parameters:
            kwargs["cancellation_token"] = active.cancellation_token
        if accepts_kwargs or "progress_callback" in parameters:
            kwargs["progress_callback"] = self._progress_callback(active.project_id)
        if accepts_kwargs or "prompt_submitted" in parameters:
            kwargs["prompt_submitted"] = True
        if accepts_kwargs or "harness" in parameters:
            kwargs["harness"] = active.adapter.harness
        result = run(active.project_id, active.prompt, **kwargs)
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

    def _finish_project(self, active: _ActiveRun, outcome: AgentRunOutcome) -> None:
        project = self.store.get_project(active.project_id)
        if project.state == ProjectState.RUNNING:
            try:
                if (
                    active.cancellation_token.cancellation_reason == "caller"
                    or outcome.cancelled
                ):
                    project = self.store.mark_stopped(
                        active.project_id,
                        outcome.failure_reason or "Agent Run stopped by user",
                        outcome.diagnostics(),
                    )
                elif outcome.validated:
                    project = self.store.mark_succeeded(
                        active.project_id, outcome.diagnostics()
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
                    project = self.store.mark_failed(
                        active.project_id,
                        _safe_reason(exc),
                        outcome.diagnostics(),
                    )
        if project.state == ProjectState.SUCCEEDED:
            self.events.append(
                active.project_id,
                stage="completed",
                tool="service",
                result="Validated Result is ready",
            )
        elif project.state == ProjectState.STOPPED:
            self.events.append(
                active.project_id,
                stage="stopped",
                tool="service",
                result="Agent Run stopped; unvalidated Scene Artifact discarded",
            )
        elif project.state == ProjectState.FAILED:
            self.events.append(
                active.project_id,
                stage="failed",
                tool="service",
                result=project.failure_reason or "Agent Run failed",
            )


def _safe_reason(error: Exception) -> str:
    first = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return redact_credentials(first)[:500] or type(error).__name__


__all__ = ["AgentRunCoordinator", "RUN_STOP_WAIT_SECONDS", "RunConflictError"]
