"""The narrow tool surface made available to the generation Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .contracts import ToolUseRecord
from .model_source import ModelSourceScaffold, create_model_source
from .references import ReferenceCatalog, ReferenceContractError


class RestrictedAgentTools:
    """Expose references, one Project Model Source, and one CAD executor.

    There is deliberately no generic path, shell, process, or cross-Project
    file operation in this class. The caller must declare the public APIs it
    used before ``execute_model`` is allowed to cross the CAD boundary; the
    declaration is checked against exact-document reads recorded in this run.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        project_dir: str | Path,
        executor: Any | None = None,
    ) -> None:
        self._catalog = ReferenceCatalog(repo_root)
        self._project_dir = Path(project_dir).expanduser().resolve()
        self._project_dir.mkdir(parents=True, exist_ok=True)
        self._executor = executor
        self._records: list[ToolUseRecord] = []
        self._skill_read = False
        self._api_index_read = False
        self._stdlib_index_read = False
        self._api_docs_read: set[str] = set()
        self._stdlib_docs_read: set[str] = set()

    @property
    def tool_use_records(self) -> tuple[ToolUseRecord, ...]:
        return tuple(self._records)

    def begin_run(self) -> ModelSourceScaffold:
        """Reset the reference gate and seed a fresh complete source scaffold."""

        self._skill_read = False
        self._api_index_read = False
        self._stdlib_index_read = False
        self._api_docs_read.clear()
        self._stdlib_docs_read.clear()
        scaffold = create_model_source(self._project_dir, overwrite=True)
        self._record("prepare_model_source", "model.py")
        return scaffold

    def read_skill_entry(self) -> str:
        content = self._catalog.read_skill_entry()
        self._skill_read = True
        self._record("read_skill_entry", "skills/simplecadapi/SKILL.md")
        return content

    def read_api_index(self) -> str:
        self._require_skill()
        content = self._catalog.read_api_index()
        self._api_index_read = True
        self._record(
            "read_api_index", "skills/simplecadapi/references/docs/api/README.md"
        )
        return content

    def read_stdlib_index(self) -> str:
        self._require_skill()
        content = self._catalog.read_stdlib_index()
        self._stdlib_index_read = True
        self._record(
            "read_stdlib_index",
            "skills/simplecadapi/references/docs/stdlib/README.md",
        )
        return content

    def read_api_doc(self, api_name: str) -> str:
        self._require_reference_indexes()
        content, target = self._catalog.read_api_doc(api_name)
        self._api_docs_read.add(self._normalize_name(api_name))
        self._record("read_api_doc", target)
        return content

    def read_stdlib_doc(self, stdlib_name: str) -> str:
        self._require_reference_indexes()
        content, target = self._catalog.read_stdlib_doc(stdlib_name)
        self._stdlib_docs_read.add(self._normalize_name(stdlib_name))
        self._record("read_stdlib_doc", target)
        return content

    def list_examples(self) -> tuple[str, ...]:
        self._require_reference_indexes()
        examples = self._catalog.list_examples()
        self._record("list_examples", "examples/")
        return examples

    def read_example(self, relative_path: str) -> str:
        self._require_reference_indexes()
        content, target = self._catalog.read_example(relative_path)
        self._record("read_example", target)
        return content

    def read_model_source(self) -> str:
        path = self._model_path()
        if not path.is_file():
            raise ReferenceContractError("current Project Model Source is missing")
        content = path.read_text(encoding="utf-8")
        self._record("read_model_source", "model.py")
        return content

    def write_model_source(self, source: str) -> None:
        if not isinstance(source, str):
            raise ReferenceContractError("Model Source must be text")
        path = self._model_path()
        path.write_text(source, encoding="utf-8")
        self._record("write_model_source", "model.py")

    def require_reference_grounding_for_write(self) -> None:
        """Require the mandatory indexes and at least one exact doc before writing.

        Issue 01 keeps the low-level source method usable for maintenance and
        repair. The generation Agent adapter uses this stricter gate so the
        primary Agent cannot write a guessed Model Source before consulting the
        packaged references.
        """

        self._require_reference_indexes()
        if not (self._api_docs_read or self._stdlib_docs_read):
            raise ReferenceContractError(
                "read exact API or stdlib documentation before writing Model Source"
            )

    def execute_model(
        self,
        *,
        api_names: Sequence[str] = (),
        stdlib_names: Sequence[str] = (),
        cancellation_token: Any | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        self._require_reference_indexes()
        normalized_apis = tuple(self._normalize_name(name) for name in api_names)
        normalized_stdlib = tuple(self._normalize_name(name) for name in stdlib_names)
        if not normalized_apis and not normalized_stdlib:
            raise ReferenceContractError(
                "execute_model requires the exact APIs used by the Model Source"
            )
        missing_apis = sorted(set(normalized_apis) - self._api_docs_read)
        missing_stdlib = sorted(set(normalized_stdlib) - self._stdlib_docs_read)
        if missing_apis or missing_stdlib:
            missing = ", ".join((*missing_apis, *missing_stdlib))
            raise ReferenceContractError(
                f"read exact API documentation before execute_model: {missing}"
            )
        executor = self._executor
        if executor is None:
            from .cad_executor import CADExecutor

            executor = CADExecutor()
            self._executor = executor
        self._record(
            "execute_model",
            "model.py",
            reference_names=(*normalized_apis, *normalized_stdlib),
        )
        execution_kwargs: dict[str, Any] = {
            "cancellation_token": cancellation_token,
        }
        if timeout_seconds is not None:
            execution_kwargs["timeout_seconds"] = timeout_seconds
        result = executor.execute(self._project_dir, **execution_kwargs)
        return result

    def _require_skill(self) -> None:
        if not self._skill_read:
            raise ReferenceContractError("read the Skill entry before its indexes")

    def _require_reference_indexes(self) -> None:
        if not (self._skill_read and self._api_index_read and self._stdlib_index_read):
            raise ReferenceContractError(
                "read the Skill entry and both API/stdlib indexes before references"
            )

    def _record(
        self,
        tool_name: str,
        target: str,
        *,
        reference_names: tuple[str, ...] = (),
    ) -> None:
        self._records.append(
            ToolUseRecord(
                sequence=len(self._records) + 1,
                tool_name=tool_name,
                target=target,
                reference_names=reference_names,
            )
        )

    def _model_path(self) -> Path:
        path = self._project_dir / "model.py"
        if path.is_symlink():
            raise ReferenceContractError("Model Source must not be a symlink")
        try:
            path.resolve().relative_to(self._project_dir)
        except ValueError as exc:
            raise ReferenceContractError("Model Source is outside the Project") from exc
        return path

    @staticmethod
    def _normalize_name(name: str) -> str:
        if not isinstance(name, str):
            raise ReferenceContractError("reference names must be text")
        return name[:-3] if name.endswith(".md") else name


__all__ = ["ReferenceContractError", "RestrictedAgentTools"]
