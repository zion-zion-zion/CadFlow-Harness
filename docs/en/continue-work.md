# Continue or retry

An Accepted version stays in the Project. Send a new message in the same
Project to request a change; the old version remains, and the new Run checks
the revision separately.

## Continue a successful Project

Describe the change relative to the existing geometry, for example, "add two
6 mm counterbores while keeping the mounting envelope." The Agent reads the
current `/code/model.py`, edits the source, and creates a new Draft bundle. A
passing result becomes the next Accepted version.

## Recover from a failure

Read the failure reason and Run Progress before retrying. Common causes include
missing model credentials, invalid Python, a failed boolean, an invalid solid, a
constraint residual, an envelope violation, or a timeout. Correct the request or
environment and send a new message. The backend does not accept an incomplete
export or an unvalidated Scene.

The **Stop Run** control requests cancellation of the active turn. A stopped Run
keeps its records and can be followed by another message.

## Clear or delete

**Clear Conversation** resets the Project conversation after name confirmation;
the Project ID stays the same. **Delete Project** permanently removes the
Project directory and its artifacts. Back up any local data you need before
deleting it.

## Inspect source outside the Viewer

The Viewer has no source editor or local ZIP picker. Inspect the `code/` and
versioned `source/` directories on disk, or download redacted trace and product
files through the API.
