# Projects and Runs

## Project

A Project is the durable boundary for one design conversation. Its directory
contains metadata, the Python source workspace, conversation records, previews,
diagnostics, and accepted artifact versions. Multiple Projects can exist in the
catalog; each has a generated hexadecimal `project_id`.

Project states are `Draft`, `Running`, `Succeeded`, `Failed`, and `Stopped`.
Only one Agent turn can be running for a Project at a time. Deleting a Project
removes its local directory after the Viewer asks for a name confirmation.

## Run and conversation turn

A Run is the Agent's execution of a submitted prompt. A Project's conversation
can contain multiple turns, so a later message can refine an accepted model or
retry a failed attempt. Each turn records the user message, selected harness,
model metadata, tool activity, progress events, outcomes, and bounded token
usage when the provider reports it.

The only currently selectable harness is `deepagents`. The harness has access
to the Project's Python source and read-only repository Skills, but not to
repository examples or private runtime directories.

## State transitions

```text
Draft -> Running -> Succeeded
                 -> Failed
                 -> Stopped
Succeeded/Failed/Stopped -> Running (new message or retry)
```

The coordinator recovers an interrupted `Running` Project as stopped when the
backend restarts. A new message can then start another turn.
