"""Request models for the FastAPI HTTP interface."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .harnesses import AgentHarness


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)


class RunProjectRequest(BaseModel):
    prompt: str
    harness: AgentHarness = AgentHarness.DEEPAGENTS


class ProjectMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    request_id: str = Field(min_length=1, max_length=128)
    retry_of: str | None = Field(default=None, min_length=1, max_length=128)
    harness: AgentHarness = AgentHarness.DEEPAGENTS


class DeleteProjectRequest(BaseModel):
    """Accept the UI's name confirmation without making it a Project ID."""

    name: str | None = Field(default=None, min_length=1)
    confirmation: str | None = Field(default=None, min_length=1)
    confirm_name: str | None = Field(default=None, min_length=1)

    @property
    def value(self) -> str | None:
        return self.confirmation or self.confirm_name or self.name


class PreviewPauseRequest(BaseModel):
    paused: bool


__all__ = [
    "CreateProjectRequest",
    "DeleteProjectRequest",
    "PreviewPauseRequest",
    "ProjectMessageRequest",
    "RunProjectRequest",
]
