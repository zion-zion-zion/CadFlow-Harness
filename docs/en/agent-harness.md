# Agent Harness

The current runtime exposes one harness identifier: `deepagents`. Its model
provider is configured through OpenAI-compatible environment variables and is
constructed by the backend with LangChain's `ChatOpenAI` integration.

## Filesystem contract

During a Run, the Agent sees two virtual roots:

- `/code/` is writable Python source for the selected Project. `model.py` is
  required; helper modules are allowed.
- `/skills/` is a read-only reference mount containing relevant repository
  Skills.

The Agent cannot use the file tools to edit Skills, browse other Projects, or
read the repository's examples. The executor creates runtime artifacts after
source validation; source code must not write them directly.

## Tool boundary

The harness is given bounded file and validation tools. It can inspect source,
write or edit Python, request a model validation, and use the returned
structured evidence to repair a failure. The backend excludes unrestricted
shell execution and sub-agent task delegation from this runtime.

## Model settings

`OPENAI_MODEL_ID` and `OPENAI_API_KEY` are required. `OPENAI_BASE_URL` selects
an OpenAI-compatible endpoint. Reasoning effort `none` uses Chat Completions;
an unset effort or another configured effort uses the Responses API. An
optional review model can be selected with `OPENAI_REVIEW_MODEL_ID`.

The per-Run wall-clock budget defaults to 1,200 seconds and is controlled by
`CADFLOW_AGENT_RUN_TIMEOUT_SECONDS`. A run can still fail earlier because the
provider, Python source, CadFlow operation, or validation gate fails.
