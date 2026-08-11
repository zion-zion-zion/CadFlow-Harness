"""Backend building blocks for the trusted local Text-to-CAD demo."""

from .cad_executor import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_BYTES,
    CADExecutor,
    CancellationToken,
    ExecutionResult,
    build_cad_environment,
    redact_credentials,
)
from .contracts import ToolUseRecord
from .agent import (
    AgentConfigurationError,
    AgentRunError,
    AgentRunOutcome,
    AgentRunService,
    AgentSettings,
    ReferenceGroundedAgent,
    build_chat_model,
    build_deep_agent,
    create_agent_tools,
)
from .model_source import (
    ARTIFACT_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    SCENE_ARTIFACT_NAME,
    ModelSourceScaffold,
    create_model_source,
)
from .references import ReferenceCatalog, ReferenceContractError
from .restricted_tools import RestrictedAgentTools
from .projects import (
    MAX_PROMPT_CHARS,
    Project,
    ProjectError,
    ProjectNotFoundError,
    ProjectState,
    ProjectStateError,
    ProjectStore,
    PromptValidationError,
)
from .scene_validation import SceneParseResult, validate_scene_artifact

__all__ = [
    "ARTIFACT_DIRECTORY_NAME",
    "AgentConfigurationError",
    "AgentRunError",
    "AgentRunOutcome",
    "AgentRunService",
    "AgentSettings",
    "CAD_EXECUTION_TIMEOUT_SECONDS",
    "CADExecutor",
    "CancellationToken",
    "DEFAULT_OUTPUT_BYTES",
    "ExecutionResult",
    "MODEL_SOURCE_NAME",
    "ModelSourceScaffold",
    "MAX_PROMPT_CHARS",
    "Project",
    "ProjectError",
    "ProjectNotFoundError",
    "ProjectState",
    "ProjectStateError",
    "ProjectStore",
    "PromptValidationError",
    "ReferenceCatalog",
    "ReferenceContractError",
    "ReferenceGroundedAgent",
    "RestrictedAgentTools",
    "SCENE_ARTIFACT_NAME",
    "SceneParseResult",
    "ToolUseRecord",
    "build_cad_environment",
    "build_chat_model",
    "build_deep_agent",
    "create_model_source",
    "create_agent_tools",
    "redact_credentials",
    "validate_scene_artifact",
]
