# Projects and Runs

## Project

A Project stores one design conversation and its files. Its directory contains
metadata, Python source, conversation records, previews, diagnostics, and
accepted artifact versions. The catalog can contain multiple Projects; each has
a generated hexadecimal `project_id`.

Project states are `Draft`, `Running`, `Succeeded`, `Failed`, and `Stopped`. A
Project can have only one active Agent turn. The Viewer asks for the Project name
before deletion, then removes its local directory.

## Run and conversation turn

A Run is the Agent's execution of one submitted prompt. A Project can contain
multiple turns, so a later message can change an accepted model or retry a failed
attempt. Each turn records the user message, harness, model metadata, tool
activity, progress events, outcome, and the token usage reported by the provider.

The only selectable harness is currently `deepagents`. It can access the
Project's Python source and read-only repository Skills, but not repository
examples or other runtime directories.

## State transitions

```text
Draft -> Running -> Succeeded
                 -> Failed
                 -> Stopped
Succeeded/Failed/Stopped -> Running (new message or retry)
```

When the backend restarts, the coordinator marks an interrupted `Running`
Project as stopped. A new message can then start another turn.
