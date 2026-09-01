# Continue or retry

An accepted result is a checkpoint, not a frozen export. Use a new message in
the same Project to request a change and preserve the existing version while
the new Run is evaluated.

## Continue a successful Project

Describe the change in terms of the existing geometry: for example, “add two
6 mm counterbores while keeping the mounting envelope.” The Agent reads the
current `/code/model.py`, edits the source, and validates a new Draft bundle.
Only a passing result becomes the next Accepted version.

## Recover from a failure

Read the failure reason and the Run Progress details before retrying. Common
causes are missing model credentials, invalid Python, a failed boolean, an
invalid solid, a constraint residual, an envelope violation, or a timeout.
Correct the request or environment, then send a new message. The backend does
not silently accept a partially exported or unvalidated Scene.

The **Stop Run** control requests cancellation of the active turn. A stopped
Run keeps its records and can be followed by another message.

## Clear or delete

**Clear Conversation** resets the conversation for a Project after name
confirmation; it does not change the Project ID. **Delete Project** permanently
removes the Project directory and its artifacts. Treat the output root as
local application data and back up anything you need before deleting it.

## Inspect source outside the Viewer

The Viewer intentionally has no source editor or local ZIP picker. Inspect the
`code/` and versioned `source/` directories on disk, or download the redacted
trace and product files through the API.
