# Text-to-CAD Generation

This context covers one-shot generation of a CAD part from a complete natural-language request. It defines the concepts shared by the generation agent, project storage, and the scene viewer.

## Language

**Project**:
A persistent unit identified by an opaque Project ID and a user-facing name. It begins without a Prompt, may receive at most one Prompt and one Agent Run, and is permanently deleted as a unit.
_Avoid_: Workspace, conversation, chat session

**Draft Project**:
A Project that has been created but has not yet received its Prompt. It persists in the Project Catalog and can be deleted.
_Avoid_: Empty conversation, pending generation

**Prompt**:
The user's complete natural-language description of the part to create. A Prompt is assumed sufficient and never becomes part of a multi-turn conversation.
_Avoid_: Message, turn, query

**Agent Run**:
The single autonomous generation attempt associated with a Project, including model construction and any bounded self-repair work. It may finish successfully, fail, or be stopped by the user.
_Avoid_: Conversation, chat, session

**Stopped Project**:
A Project whose Agent Run was terminated by the user. Its Prompt, current Model Source, Progress Events, and diagnostic logs remain available, but any unvalidated Scene Artifact is not a result.
_Avoid_: Paused project, failed project, deleted project

**Project State**:
The lifecycle state of a Project: Draft, Running, Succeeded, Failed, or Stopped. Deletion removes the Project and is not a state.
_Avoid_: Chat status, generation history

**Model Source**:
The generated set of SimpleCADAPI Python source files that defines the requested CAD model. The current single-part scope has one entry file; future complex models may contain project-local modules.
_Avoid_: Snippet, response, generated text

**Generated Part**:
The single physical part produced by a Project, represented by exactly one finite, positive-volume solid.
_Avoid_: Assembly, multi-body model, scene

**Validated Result**:
A Generated Part and Scene Artifact that have passed the execution, geometry, and scene-loading checks. Partial or unvalidated artifacts are never presented as a result.
_Avoid_: Agent response, draft model, partial scene

**Scene Artifact**:
The self-contained `.scene.zip` produced by executing the Model Source and consumed by the scene viewer.
_Avoid_: Preview, image, mesh file

**Generation Stage**:
A coarse, user-visible indication of the Agent Run's current progress.
_Avoid_: Token stream, chat status

**Progress Event**:
A retained, replayable server-sent update describing a Generation Stage or Agent tool activity. It reports operational progress rather than a streamed natural-language answer.
_Avoid_: Token, assistant message, chain of thought

**Project Catalog**:
The user-visible collection of all stored Projects, used to switch the page's current Project. It reflects Project persistence, not Prompt or conversation history.
_Avoid_: Chat history, conversation list, generation history
