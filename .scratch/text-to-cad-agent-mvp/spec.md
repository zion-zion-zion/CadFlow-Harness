# 实现单轮 Text-to-CAD Agent MVP

Status: ready-for-agent

## Problem Statement

用户目前只有 SimpleCADAPI SDK、示例和一个独立 Scene Viewer，缺少从自然语言 Prompt 到可运行 Model Source、Validated Result 和可交互渲染的完整 Agent 闭环。用户需要一个最小但真实的本机 Demo：创建 Project、提交一次完整零件描述后，系统自行查文档、生成 SimpleCADAPI Python、执行、修复、验证并渲染，不要求用户补充信息，也不引入多轮对话、数据库、队列或复杂部署。

## Solution

提供一个可信本机、单用户、单轮的 Text-to-CAD Agent 工作区。页面采用 Project Catalog、当前 Project 操作区和 CAD Viewer 三栏布局。FastAPI 管理持久 Project、REST 控制接口、可重放 SSE Progress Event 和单个后台 Agent Run。LangChain Deep Agents 主 Agent 通过受限工具按需阅读 SimpleCADAPI Skill 与示例，在后端提供的 Model Source 骨架上编写一个单零件程序，最多执行三次并根据结构化错误自动修复。通过退出码、单 Solid、正体积和 canonical Scene Artifact 解析检查后，Viewer 自动加载结果。

## User Stories

1. As a CAD Demo user, I want to create a named Draft Project, so that each modeling request has an isolated persistent home.
2. As a CAD Demo user, I want Project names to be human-readable and allowed to repeat, so that naming does not become an identity constraint.
3. As a CAD Demo user, I want every Project to have an opaque unique ID, so that duplicate names and path characters cannot cause collisions.
4. As a CAD Demo user, I want to see all stored Projects in a Project Catalog, so that I can switch among existing work.
5. As a CAD Demo user, I want the Project Catalog sorted by recent activity, so that the Project I just used is easy to find.
6. As a CAD Demo user, I want multiple Draft Projects to persist across refreshes, so that merely closing the page does not discard them.
7. As a CAD Demo user, I want to submit one complete plain-text Prompt to a Draft Project, so that the system can generate a part without conversation setup.
8. As a CAD Demo user, I want empty and excessively long Prompts rejected before execution, so that accidental submissions do not start an Agent Run.
9. As a keyboard user, I want Cmd/Ctrl+Enter to submit the Prompt, so that generation can start without leaving the text field.
10. As a CAD Demo user, I want the submitted Prompt to become read-only, so that the persistent Project accurately records what produced the result.
11. As a CAD Demo user, I want missing dimensions or construction details inferred automatically, so that the run never pauses for clarification.
12. As a CAD Demo user, I want unspecified lengths interpreted as millimetres, so that Agent assumptions are consistent.
13. As a CAD Demo user, I want key inferred assumptions recorded in the Model Source, so that I can understand how an underspecified Prompt was resolved.
14. As a CAD Demo user, I want generation to read the packaged SimpleCADAPI Skill and precise API references, so that Model Source follows the SDK contract rather than guessing APIs.
15. As a CAD Demo user, I want generation to consult relevant repository examples, so that established modeling workflows are reused.
16. As a CAD Demo user, I want to see curated Progress Events while the Agent works, so that I know whether it is reading, writing, executing, repairing, or validating.
17. As a CAD Demo user, I want Progress Events restored after switching Projects or refreshing, so that the visible timeline remains coherent.
18. As a CAD Demo user, I want raw chain of thought, model tokens, full tool arguments, and complete process logs hidden, so that the page remains a product UI rather than a debug console.
19. As a CAD Demo user, I want to switch to another Project while an Agent Run continues, so that viewing older results does not interrupt active work.
20. As a CAD Demo user, I want only one global Agent Run at a time, so that local CAD and model resources are not contended.
21. As a CAD Demo user, I want other Draft Projects prevented from starting while one run is active, so that the single-run rule is visible and deterministic.
22. As a CAD Demo user, I want a one-click Stop control during Running, so that I can terminate unwanted generation immediately.
23. As a CAD Demo user, I want Stop to cancel both the Deep Agent and any active CAD subprocess, so that background work truly ends.
24. As a CAD Demo user, I want a Stopped Project to retain its Prompt, latest Model Source, Progress Events, and diagnostics, so that termination does not erase useful evidence.
25. As a CAD Demo user, I want unvalidated partial scenes hidden after Stop or failure, so that incomplete output is never mistaken for a result.
26. As a CAD Demo user, I want permanent deletion to require confirmation with the Project name, so that accidental data loss is less likely.
27. As a CAD Demo user, I want deleting a Running Project to cancel its work before removing its directory, so that no orphan process survives.
28. As a CAD Demo user, I want deletion to remove the Project immediately from the Catalog, so that the UI reflects hard-delete semantics.
29. As a CAD Demo user, I want a failed run to show a concise failure reason, so that I understand the outcome without reading a traceback.
30. As a CAD developer, I want detailed bounded logs retained on disk, so that failed execution can be diagnosed locally.
31. As a CAD Demo user, I want the Agent to repair failed Model Source automatically, so that ordinary API or geometry mistakes do not require intervention.
32. As a CAD Demo user, I want repair bounded to three total CAD executions and five minutes, so that a broken Agent Run cannot loop indefinitely.
33. As a CAD Demo user, I want transient model-provider errors retried separately, so that network hiccups do not consume CAD repair attempts.
34. As a CAD Demo user, I want a Project marked Succeeded only when one finite positive-volume Solid and a valid Scene Artifact exist, so that successful results are technically usable.
35. As a CAD Demo user, I want a successful Project to load its Scene Artifact automatically, so that no manual file selection is required.
36. As a CAD Demo user, I want Draft, Running, Failed, and Stopped Projects to show their own empty Viewer state, so that another Project's geometry is never shown under the wrong Prompt.
37. As a CAD Demo user, I want to rotate, pan, zoom, fit, and automatically frame the generated part, so that I can inspect it interactively.
38. As a CAD Demo user, I want the Viewer to focus only on the rendered result, so that model trees, inspectors, source editors, and local ZIP upload do not distract from the Demo.
39. As a CAD developer, I want the generated Model Source persisted in the Project directory, so that the SimpleCADAPI Python deliverable exists independently of the UI.
40. As a CAD developer, I want only the latest Model Source retained, so that MVP storage does not become a source-control system.
41. As a CAD developer, I want each execution's stdout and stderr bounded and credential-like text redacted, so that diagnostics remain useful without growing indefinitely or exposing secrets.
42. As a local operator, I want completed Projects reconstructed from disk after service restart, so that Project persistence does not depend on process memory.
43. As a local operator, I want an interrupted Running Project marked Failed on restart, so that the UI never claims an orphan Agent is still working.
44. As a local operator, I want model API configuration supplied only through backend environment variables, so that Provider credentials never enter the browser.
45. As a local operator, I want CAD subprocesses launched without model API credentials in their environment, so that generated geometry code does not receive unnecessary secrets.
46. As a local operator, I want the service bound to localhost and served same-origin, so that directly executed generated Python is not exposed to untrusted network users.
47. As a maintainer, I want pure state, storage, and Scene validation behavior testable without a model request, so that deterministic logic remains fast to verify.
48. As a maintainer, I want every Agent integration test to use the real configured model rather than a Fake Agent, so that the autonomous generation claim is tested honestly.
49. As a maintainer, I want real Agent tests opt-in through a live marker, so that normal test runs do not incur model cost or fail when the Provider is unavailable.
50. As a maintainer, I want a fixed flange Prompt for the live smoke test, so that the complete Prompt-to-render path has a repeatable demonstration case.

## Implementation Decisions

- Use the domain language defined for Project, Draft Project, Prompt, Agent Run, Project State, Model Source, Generated Part, Validated Result, Scene Artifact, Generation Stage, Progress Event, and Project Catalog.
- Limit MVP output to one physical part represented by exactly one Solid. A Project receives at most one Prompt and one Agent Run.
- Use Project states Draft, Running, Succeeded, Failed, and Stopped. Failed and Stopped Projects cannot run again. Deletion removes the Project rather than introducing another state.
- Build the backend with FastAPI in one Uvicorn worker. Keep active task handles, CAD process handles, cancellation, and a global single-run lock in process memory.
- Persist durable Project data on the filesystem. Each Project retains metadata, Prompt, latest Model Source, replayable Progress Events, bounded per-attempt logs, and a canonical Scene Artifact after success.
- Rebuild the Project Catalog from filesystem metadata at startup. Convert any persisted Running state to Failed instead of implementing Agent checkpoint recovery.
- Expose the following API contract: GET and POST `/api/projects`; GET and DELETE `/api/projects/{project_id}`; POST `/api/projects/{project_id}/run`; POST `/api/projects/{project_id}/stop`; GET `/api/projects/{project_id}/events`; and GET `/api/projects/{project_id}/scene`.
- A run request is valid only for Draft Projects and returns a conflict while another Agent Run is active. A stop request is valid only for Running Projects. A Scene request succeeds only for Succeeded Projects.
- Deliver Progress Events through server-sent events with keepalives, monotonically increasing event IDs, persistence, and replay after Last-Event-ID.
- Send only curated stage, tool, attempt, and short-result information through SSE. Do not expose natural-language model streaming, chain of thought, full tool arguments, raw logs, or model credentials.
- Use LangChain Deep Agents as a hard requirement. Use one primary Agent with run-local planning, no subagents, and no cross-Project memory.
- Seed a complete Model Source scaffold before Agent work. Require one SimpleCADAPI model entry point, one captured final Solid, the Project artifact directory, and canonical `model.scene.zip` generation.
- Allow the Agent to edit the entire current Model Source so it can add imports and helper functions. Current MVP guidance permits Python standard-library modules and SimpleCADAPI only.
- Give the Agent read-only access to the packaged SimpleCADAPI Skill, exact API documentation, and repository examples; give it Project-scoped source read/write access and one dedicated `execute_model` tool. Do not expose a general Shell.
- Require the Agent to read the Skill entry document, required API/stdlib indexes, and the exact documentation for each API it chooses.
- Do not perform AST, import, or dangerous-call inspection before running Model Source. Treat allowed imports as an Agent instruction rather than a security boundary.
- Execute Model Source with the backend's Python interpreter in the Project working directory. Remove model API credentials from the subprocess environment, cap output, enforce a 120-second per-execution timeout, and support process termination.
- Permit at most three total CAD executions during a five-minute Agent Run. Retry explicitly transient model API failures at most twice without consuming CAD execution attempts.
- Return a structured `execute_model` result containing execution status, capped error information, captured Solid count, volume, Scene Artifact existence, and Scene parsing result.
- Promote output to a Validated Result only when the subprocess exits successfully, exactly one finite positive-volume Solid is captured, only the expected canonical Scene Artifact is present, and the existing Viewer package validation can parse it.
- Do not add semantic or visual model judging in MVP.
- Keep the existing Vite, native TypeScript, and Three.js frontend stack. Refactor reusable Scene package loading and rendering away from the page entry point.
- Use a desktop three-column layout: Project Catalog, selected Project Prompt/progress/control panel, and CAD Viewer.
- Preserve canonical Scene ZIP unpacking, manifest and hash validation, GLB loading, camera controls, auto framing, and Fit. Remove model navigation, Inspector, entity selection, source dock, CodeMirror, local package upload, and unrelated Viewer controls.
- Bind the service to `127.0.0.1`. Serve the production frontend from FastAPI on the same origin, use a Vite API proxy during development, and do not enable CORS.
- Configure one OpenAI-compatible model through backend environment variables. Do not expose Provider, model, endpoint, or key settings in the UI.
- Do not add a database, task queue, multi-worker coordination, authentication, container sandbox, or public deployment path in MVP.

## Testing Decisions

- Test externally visible behavior at the highest practical seam. The primary seam is the FastAPI application boundary: drive Project creation, Prompt submission, Project State transitions, SSE replay, Stop, deletion, restart recovery, and Scene Artifact retrieval through HTTP rather than asserting internal method calls.
- Keep one subordinate CAD seam at the dedicated `execute_model` contract. Test successful and failing Model Source execution through its structured observable result, including timeout, process exit, Solid cardinality, volume, expected artifact count, and Scene package parsing.
- Test pure Project metadata, state transition, filesystem persistence, event replay, redaction, and Scene validation code without a model request. These deterministic units do not need a Fake Agent.
- Do not use a Fake Agent for Agent integration or end-to-end tests. Every test that claims to exercise generation must call the configured real model.
- Mark real Agent tests `live_agent` and exclude them from the default test selection. Run them only when explicitly requested with valid model environment configuration.
- Use the fixed live smoke Prompt: “创建一个外径 80 mm、厚 10 mm、中心孔直径 30 mm、节圆直径 60 mm、均布 6 个直径 6 mm 通孔的圆形法兰盘，所有边缘做 1 mm 倒角。”
- The live smoke test must cross the complete service seam, reach Succeeded, return a canonical Scene Artifact, and load it through the Viewer path. It must not assert model token sequences, exact tool-call ordering, or implementation-private Agent state.
- Cover cancellation with a real active Agent Run and observe Stopped plus child-process termination. Cover deletion through the API and observe removal from the Project Catalog and filesystem.
- Cover service restart by persisting representative terminal Projects and an interrupted Running Project, recreating the service, and observing Catalog reconstruction plus Running-to-Failed recovery.
- Run the ordinary Python test suite without live model calls. Run live Agent tests explicitly. Type-check and build the Viewer as the minimum frontend automated check, then record a manual visual verification for the three-column layout and interactive CAD controls.
- Prefer existing seams: canonical Scene ZIP validation and SimpleCADAPI model/export behavior already exercised by repository examples. The backend currently has no prior tests, so the new HTTP application boundary should remain the dominant test surface rather than introducing multiple internal mocking seams.

## Out of Scope

- Multi-turn conversation, clarification questions, Prompt editing after submission, or rerunning the same Project.
- Multiple parts, assemblies, current-MVP multi-file generation, and Project-local imports. Future complex assembly support may adopt the existing modular example style.
- Image, sketch, STEP, BREP, or other attachment input and inverse-engineering workflows.
- STEP, STL, FCStd, or other manufacturing/export formats beyond canonical Scene ZIP.
- Model Source viewing, editing, or downloading in the browser.
- Visual-language-model judging, semantic similarity scoring, or automated Prompt-compliance ranking.
- Mobile-specific responsive design, React/Vue migration, database storage, Redis/Celery, multiple Uvicorn workers, authentication, CORS, public or LAN deployment, container sandboxing, and source static security analysis.
- Source version history, Git commits per Agent attempt, automatic Project expiry, recycle bin, pause/resume, and recovery of an interrupted Agent Run.

## Further Notes

- The existing backend is a scaffold; the existing Viewer already supplies the canonical Scene ZIP parser, integrity checks, Three.js scene construction, and camera/render loop that should be preserved.
- The packaged SimpleCADAPI Skill imposes mandatory exact-document lookup, keyword arguments, one model entry point, explicit capture, incremental validation, and one-part-per-file discipline.
- ADR-0001 limits generated-code execution to a trusted localhost Demo. ADR-0002 requires a bounded Deep Agents loop. ADR-0003 defines Scene ZIP as the rendering boundary. ADR-0004 records the deliberate absence of static source inspection. ADR-0005 keeps orchestration in one service process.
- The security posture is operational control, not isolation. Project-scoped working directories, timeouts, output caps, credential-stripped subprocess environments, and cancellation do not make arbitrary Python safe for untrusted users.
- This specification is implementation-ready under the `ready-for-agent` triage role.
