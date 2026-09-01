# Agent Harness

The runtime currently exposes one harness identifier: `deepagents`. Configure
the model with OpenAI-compatible environment variables; the backend creates it
through LangChain's `ChatOpenAI` integration.

## Filesystem contract

During a Run, the Agent sees two virtual roots:

- `/code/` is writable Python source for the selected Project. `model.py` is
  required; helper modules are allowed.
- `/skills/` is a read-only reference mount containing repository Skills.

The Agent cannot use the file tools to edit Skills, browse other Projects, or
read repository examples. After source validation, the executor creates runtime
artifacts. Source code must not write those artifacts directly.

## Tool boundary

The harness has restricted file and validation tools. It can inspect source,
write or edit Python, request model validation, and use structured results to
repair a failure. The runtime does not expose unrestricted shell commands or
sub-agent delegation.

## Model settings

`OPENAI_MODEL_ID` and `OPENAI_API_KEY` are required. `OPENAI_BASE_URL` selects
the OpenAI-compatible endpoint. Reasoning effort `none` uses Chat Completions;
an unset effort or another configured effort uses the Responses API. Set
`OPENAI_REVIEW_MODEL_ID` to use a separate review model.

The default wall-clock budget for a Run is 1,200 seconds, controlled by
`CADFLOW_AGENT_RUN_TIMEOUT_SECONDS`. A provider error, Python error, CadFlow
operation, or failed validation gate can end a Run earlier.
