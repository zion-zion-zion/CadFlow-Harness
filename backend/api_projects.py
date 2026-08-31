"""Project, Conversation, and Agent Run HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from .api_models import (
    CreateProjectRequest,
    DeleteProjectRequest,
    ProjectMessageRequest,
    RunProjectRequest,
)
from .api_payloads import get_project_or_404, project_payload, scene_available
from .projects import (
    ProjectError,
    ProjectNotFoundError,
    ProjectStateError,
    ProjectStore,
    PromptValidationError,
)
from .run_coordinator import AgentRunCoordinator, RunConflictError


def create_project_router(
    *,
    project_store: ProjectStore,
    coordinator: AgentRunCoordinator,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects")
    def list_projects() -> list[dict[str, object]]:
        return [
            project_payload(project_store, project)
            for project in project_store.list_projects()
        ]

    @router.post("/api/projects", status_code=201)
    def create_project(request: CreateProjectRequest) -> dict[str, object]:
        try:
            project = project_store.create_project(request.name)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return project_payload(project_store, project)

    @router.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, object]:
        project = get_project_or_404(project_store, project_id)
        return project_payload(project_store, project)

    @router.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, request: DeleteProjectRequest) -> Response:
        project = get_project_or_404(project_store, project_id)
        confirmation = request.value
        if confirmation is None:
            raise HTTPException(
                status_code=422,
                detail="Project name confirmation is required",
            )
        if confirmation != project.name:
            raise HTTPException(
                status_code=409,
                detail="Project name confirmation does not match",
            )
        try:
            coordinator.delete(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @router.post("/api/projects/{project_id}/run", status_code=202)
    def start_run(
        project_id: str, request: RunProjectRequest
    ) -> dict[str, object]:
        try:
            project = coordinator.start(project_id, request.prompt, request.harness)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PromptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return project_payload(project_store, project)

    @router.get("/api/projects/{project_id}/messages")
    def project_messages(project_id: str) -> JSONResponse:
        get_project_or_404(project_store, project_id)
        return JSONResponse(
            {
                "conversation_id": project_id,
                "turns": project_store.conversation_turns(project_id),
                "current_artifact_version": (
                    project_store.current_artifact_version(project_id)
                ),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/projects/{project_id}/messages")
    def create_project_message(
        project_id: str,
        request: ProjectMessageRequest,
    ) -> dict[str, object]:
        try:
            submission = coordinator.start_message(
                project_id,
                request.message,
                request_id=request.request_id,
                retry_of=request.retry_of,
                harness=request.harness,
            )
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PromptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        coordinator.wait_for_turn(submission.turn_id)
        project = project_store.get_project(project_id)
        turn = project_store.conversation_log(project_id).turn(submission.turn_id)
        if turn is None:
            raise HTTPException(
                status_code=500, detail="Conversation turn was not persisted"
            )
        return {
            "turn": turn,
            "project": project_payload(project_store, project),
            "artifact": {
                "version": project_store.current_artifact_version(project_id),
                "scene_available": scene_available(project_store, project),
            },
            "duplicate": submission.duplicate,
        }

    @router.delete("/api/projects/{project_id}/conversation")
    def clear_project_conversation(
        project_id: str,
        request: DeleteProjectRequest,
    ) -> dict[str, object]:
        project = get_project_or_404(project_store, project_id)
        confirmation = request.value
        if confirmation is None:
            raise HTTPException(
                status_code=422,
                detail="Project name confirmation is required",
            )
        if confirmation != project.name:
            raise HTTPException(
                status_code=409,
                detail="Project name confirmation does not match",
            )
        try:
            reset = project_store.clear_conversation(project_id)
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return project_payload(project_store, reset)

    @router.post("/api/projects/{project_id}/stop")
    def stop_run(project_id: str) -> dict[str, object]:
        try:
            project = coordinator.stop(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return project_payload(project_store, project)

    return router


__all__ = ["create_project_router"]
