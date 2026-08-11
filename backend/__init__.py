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
from .model_source import (
    ARTIFACT_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    SCENE_ARTIFACT_NAME,
    ModelSourceScaffold,
    create_model_source,
)
from .references import ReferenceCatalog, ReferenceContractError
from .restricted_tools import RestrictedAgentTools
from .scene_validation import SceneParseResult, validate_scene_artifact

__all__ = [
    "ARTIFACT_DIRECTORY_NAME",
    "CAD_EXECUTION_TIMEOUT_SECONDS",
    "CADExecutor",
    "CancellationToken",
    "DEFAULT_OUTPUT_BYTES",
    "ExecutionResult",
    "MODEL_SOURCE_NAME",
    "ModelSourceScaffold",
    "ReferenceCatalog",
    "ReferenceContractError",
    "RestrictedAgentTools",
    "SCENE_ARTIFACT_NAME",
    "SceneParseResult",
    "ToolUseRecord",
    "build_cad_environment",
    "create_model_source",
    "redact_credentials",
    "validate_scene_artifact",
]
