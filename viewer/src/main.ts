import './style.css';

import { ScenePackageError, SceneViewer } from './components/scene-viewer';

const MAX_PROMPT_CHARS = 32_000;

type ProjectState = 'Draft' | 'Running' | 'Succeeded' | 'Failed' | 'Stopped';
type AgentHarness = 'deepagents' | 'pi';
type HarnessStatus = {
  id: AgentHarness;
  label: string;
  available: boolean;
  implementation_version: string;
  unavailable_reason: string | null;
};
type TokenUsage = {
  input_tokens: number | null;
  cached_input_tokens: number | null;
  uncached_input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
};
type Project = {
  project_id: string;
  name: string;
  state: ProjectState;
  created_at: string;
  updated_at: string;
  prompt: string | null;
  failure_reason: string | null;
  harness: AgentHarness;
  scene_available: boolean;
  diagnostics_available: boolean;
  duration_seconds: number | null;
  token_usage: TokenUsage | null;
};
type ProgressRecord = {
  id: number;
  created_at: string;
  stage: string;
  tool: string | null;
  attempt: number | null;
  result: string | null;
  preview?: {
    attempt: number;
    revision: number;
    operation: string;
  };
};

const app = document.querySelector<HTMLDivElement>('#app');
if (!app) throw new Error('viewer root is missing');

app.innerHTML = `
  <main class="shell">
    <header class="topbar">
      <div class="topbar-leading">
        <div class="brand">
          <span class="brand-mark">CF</span>
          <div><strong>CadFlowAgent</strong><span>local text-to-cad workspace</span></div>
        </div>
        <div id="harness-selector" class="harness-selector" role="group" aria-label="Agent harness">
          <button class="harness-option" type="button" data-harness="deepagents">Deep Agents</button>
          <button class="harness-option" type="button" data-harness="pi">Pi</button>
        </div>
      </div>
      <div class="topbar-actions">
        <span class="local-badge">LOCAL DEMO</span>
        <button id="refresh-projects" class="quiet-button" type="button">Refresh</button>
      </div>
    </header>
    <section class="workspace">
      <aside class="catalog-panel panel" aria-label="Project Catalog">
        <div class="panel-heading">
          <div><span class="eyebrow">PROJECT CATALOG</span><h1>Projects</h1></div>
          <span id="project-count" class="count">0</span>
        </div>
        <form id="create-project-form" class="create-project-form">
          <label for="new-project-name">New Project</label>
          <div class="inline-form">
            <input id="new-project-name" type="text" maxlength="160" placeholder="Project name" autocomplete="off" />
            <button class="primary-icon-button" type="submit" aria-label="Create Project">+</button>
          </div>
        </form>
        <div id="project-list" class="project-list" role="list"></div>
        <p id="catalog-empty" class="panel-empty">No Projects yet. Create a Draft Project to begin.</p>
      </aside>

      <section class="project-panel panel" aria-label="Current Project">
        <div id="project-empty" class="empty-workspace">
          <span class="empty-mark">＋</span>
          <strong>Create or select a Project</strong>
          <span>A Project keeps one Prompt, one Agent Run and its Validated Result together.</span>
        </div>
        <div id="project-content" hidden>
          <div class="panel-heading current-heading">
            <div><span class="eyebrow">CURRENT PROJECT</span><h1 id="project-name"></h1><code id="project-id"></code></div>
            <div class="project-heading-meta"><span id="harness-metadata" class="harness-metadata"></span><span id="state-badge" class="state-badge"></span></div>
          </div>
          <dl class="run-metrics" aria-label="Agent Run metrics">
            <div><dt>Total Tokens</dt><dd id="total-tokens">--</dd></div>
            <div><dt>Input Tokens</dt><dd id="input-tokens">--</dd></div>
            <div><dt>Cached Tokens</dt><dd id="cached-tokens">--</dd></div>
            <div><dt>Uncached Tokens</dt><dd id="uncached-tokens">--</dd></div>
            <div><dt>Output Tokens</dt><dd id="output-tokens">--</dd></div>
            <div><dt>Total Time</dt><dd id="run-time">--</dd></div>
          </dl>
          <div class="project-controls">
            <label for="prompt-input">Prompt</label>
            <textarea id="prompt-input" rows="7" maxlength="32000" placeholder="Describe one complete CAD part..."></textarea>
            <div class="prompt-footer"><span id="prompt-counter">0 / 32000</span><span>Cmd/Ctrl + Enter to submit</span></div>
            <div class="control-row">
              <button id="run-button" class="primary-button" type="button">Start Agent Run</button>
              <button id="stop-button" class="warning-button" type="button" hidden>Stop Run</button>
              <button id="delete-button" class="danger-button" type="button">Delete Project</button>
            </div>
            <p id="action-message" class="action-message" role="status"></p>
          </div>
          <section class="progress-section" aria-label="Agent Run progress">
            <div class="section-heading"><span class="eyebrow">PROGRESS</span><span id="progress-count" class="count">0</span></div>
            <div id="progress-list" class="progress-list"><p class="panel-empty">No Agent Run yet.</p></div>
          </section>
        </div>
      </section>

      <section class="viewer-panel" aria-label="CAD Viewer">
        <div class="viewer-heading"><div><span class="eyebrow">CAD VIEWER</span><strong>Validated Result</strong></div><button id="fit-button" class="quiet-button" type="button">Fit</button></div>
        <div id="viewer-stage" class="viewer-stage">
          <div id="viewer" class="viewer"></div>
          <div id="viewer-loading" class="viewer-loading" hidden><span class="spinner"></span><span>Loading Scene Artifact</span></div>
          <div id="viewer-empty" class="viewer-empty"><span class="empty-mark">◇</span><strong id="viewer-empty-title">No Project selected</strong><span id="viewer-empty-copy">Select a Project to inspect its own Scene Artifact.</span></div>
          <div class="viewer-hint">Drag to rotate · right-drag to pan · scroll to zoom</div>
          <div id="viewer-status" class="viewer-status"><span id="viewer-status-dot" class="status-dot"></span><span id="viewer-status-text">Waiting for a Validated Result</span></div>
        </div>
      </section>
    </section>
    <footer class="footer"><span>Trusted localhost demo · generated code runs in a bounded local process</span><span id="service-message"></span></footer>
  </main>`;

const projectListElement = document.querySelector<HTMLDivElement>('#project-list')!;
const projectCount = document.querySelector<HTMLSpanElement>('#project-count')!;
const catalogEmpty = document.querySelector<HTMLParagraphElement>('#catalog-empty')!;
const projectEmpty = document.querySelector<HTMLDivElement>('#project-empty')!;
const projectContent = document.querySelector<HTMLDivElement>('#project-content')!;
const projectName = document.querySelector<HTMLHeadingElement>('#project-name')!;
const projectId = document.querySelector<HTMLElement>('#project-id')!;
const stateBadge = document.querySelector<HTMLSpanElement>('#state-badge')!;
const harnessMetadata = document.querySelector<HTMLSpanElement>('#harness-metadata')!;
const harnessSelector = document.querySelector<HTMLDivElement>('#harness-selector')!;
const harnessButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('.harness-option'));
const totalTokens = document.querySelector<HTMLElement>('#total-tokens')!;
const inputTokens = document.querySelector<HTMLElement>('#input-tokens')!;
const cachedTokens = document.querySelector<HTMLElement>('#cached-tokens')!;
const uncachedTokens = document.querySelector<HTMLElement>('#uncached-tokens')!;
const outputTokens = document.querySelector<HTMLElement>('#output-tokens')!;
const runTime = document.querySelector<HTMLElement>('#run-time')!;
const promptInput = document.querySelector<HTMLTextAreaElement>('#prompt-input')!;
const promptCounter = document.querySelector<HTMLSpanElement>('#prompt-counter')!;
const runButton = document.querySelector<HTMLButtonElement>('#run-button')!;
const stopButton = document.querySelector<HTMLButtonElement>('#stop-button')!;
const deleteButton = document.querySelector<HTMLButtonElement>('#delete-button')!;
const actionMessage = document.querySelector<HTMLParagraphElement>('#action-message')!;
const progressCount = document.querySelector<HTMLSpanElement>('#progress-count')!;
const progressList = document.querySelector<HTMLDivElement>('#progress-list')!;
const viewerElement = document.querySelector<HTMLDivElement>('#viewer')!;
const viewerLoading = document.querySelector<HTMLDivElement>('#viewer-loading')!;
const viewerEmpty = document.querySelector<HTMLDivElement>('#viewer-empty')!;
const viewerEmptyTitle = document.querySelector<HTMLElement>('#viewer-empty-title')!;
const viewerEmptyCopy = document.querySelector<HTMLElement>('#viewer-empty-copy')!;
const viewerStatusText = document.querySelector<HTMLSpanElement>('#viewer-status-text')!;
const viewerStatusDot = document.querySelector<HTMLSpanElement>('#viewer-status-dot')!;
const serviceMessage = document.querySelector<HTMLSpanElement>('#service-message')!;

let projects: Project[] = [];
let selectedProjectId: string | null = null;
let selectedVersion = 0;
let progressByProject = new Map<string, ProgressRecord[]>();
let eventSource: EventSource | null = null;
let sceneRequest: AbortController | null = null;
let previewRequest: AbortController | null = null;
let previewProjectId: string | null = null;
let latestPreviewAttempt = 0;
let latestPreviewRevision = 0;
let loadedSceneProjectId: string | null = null;
let workspaceMessage = '';
let selectedHarness: AgentHarness = 'deepagents';
let harnessStatuses: HarnessStatus[] = [
  { id: 'deepagents', label: 'Deep Agents', available: true, implementation_version: '', unavailable_reason: null },
  { id: 'pi', label: 'Pi', available: false, implementation_version: '', unavailable_reason: 'Pi worker status is unavailable.' },
];

const sceneViewer = new SceneViewer(viewerElement, (message, ready) => {
  viewerStatusText.textContent = message;
  viewerStatusDot.classList.toggle('ready', ready);
});

function currentProject(): Project | null {
  return projects.find((project) => project.project_id === selectedProjectId) ?? null;
}

function globalRunActive(): boolean {
  return projects.some((project) => project.state === 'Running');
}

function harnessStatus(id: AgentHarness): HarnessStatus {
  return harnessStatuses.find((item) => item.id === id)
    ?? { id, label: id === 'pi' ? 'Pi' : 'Deep Agents', available: id === 'deepagents', implementation_version: '', unavailable_reason: null };
}

function selectedHarnessFor(project: Project | null): AgentHarness {
  return project && project.state !== 'Draft' ? project.harness : selectedHarness;
}

function renderHarnessSelector(): void {
  const project = currentProject();
  const selected = selectedHarnessFor(project);
  const locked = project === null || project.state !== 'Draft' || globalRunActive();
  for (const button of harnessButtons) {
    const id = button.dataset.harness as AgentHarness;
    const status = harnessStatus(id);
    const unavailable = !status.available;
    button.classList.toggle('selected', id === selected);
    button.classList.toggle('unavailable', unavailable);
    button.setAttribute('aria-pressed', String(id === selected));
    button.disabled = locked || (unavailable && id !== selected);
    button.title = unavailable ? (status.unavailable_reason ?? `${status.label} is unavailable.`) : status.label;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload !== null && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return payload as T;
}

function upsertProject(project: Project): void {
  const index = projects.findIndex((item) => item.project_id === project.project_id);
  if (index >= 0) projects[index] = project;
  else projects.unshift(project);
  projects.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

function renderCatalog(): void {
  projectCount.textContent = String(projects.length);
  catalogEmpty.hidden = projects.length > 0;
  projectListElement.replaceChildren();
  for (const project of projects) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'project-row';
    row.classList.toggle('selected', project.project_id === selectedProjectId);
    row.setAttribute('role', 'listitem');
    row.setAttribute('aria-current', String(project.project_id === selectedProjectId));
    const title = document.createElement('strong');
    title.textContent = project.name;
    const meta = document.createElement('span');
    meta.className = 'project-row-meta';
    const state = document.createElement('span');
    state.textContent = project.state;
    state.className = `project-state state-${project.state.toLowerCase()}`;
    const usage = document.createElement('span');
    usage.className = 'project-usage';
    usage.textContent = `${formatCompactTokenCount(project.token_usage?.total_tokens ?? null)} tok · ${formatDuration(project.duration_seconds)}`;
    meta.append(state, usage);
    row.append(title, meta);
    row.addEventListener('click', () => void selectProject(project.project_id));
    projectListElement.append(row);
  }
}

function renderWorkspace(): void {
  const project = currentProject();
  const hasProject = project !== null;
  projectEmpty.hidden = hasProject;
  projectContent.hidden = !hasProject;
  if (!project) {
    promptInput.value = '';
    promptInput.readOnly = true;
    runButton.disabled = true;
    stopButton.hidden = true;
    deleteButton.disabled = true;
    actionMessage.textContent = '';
    harnessMetadata.textContent = '';
    renderRunMetrics(null);
    progressList.innerHTML = '<p class="panel-empty">No Project selected.</p>';
    return;
  }

  projectName.textContent = project.name;
  projectId.textContent = project.project_id;
  harnessMetadata.textContent = harnessStatus(project.harness).label;
  stateBadge.textContent = project.state;
  stateBadge.className = `state-badge state-${project.state.toLowerCase()}`;
  renderRunMetrics(project);
  if (document.activeElement !== promptInput || promptInput.readOnly) promptInput.value = project.prompt ?? '';
  promptInput.readOnly = project.state !== 'Draft';
  promptCounter.textContent = `${promptInput.value.length.toLocaleString()} / ${MAX_PROMPT_CHARS.toLocaleString()}`;

  const anotherRunIsActive = globalRunActive() && project.state === 'Draft';
  runButton.hidden = project.state !== 'Draft';
  runButton.disabled = project.state !== 'Draft' || anotherRunIsActive;
  stopButton.hidden = project.state !== 'Running';
  stopButton.disabled = project.state !== 'Running';
  deleteButton.disabled = false;
  if (workspaceMessage) actionMessage.textContent = workspaceMessage;
  else if (anotherRunIsActive) actionMessage.textContent = 'Another Agent Run is active. This Draft will remain available.';
  else if (project.state === 'Failed' || project.state === 'Stopped') actionMessage.textContent = project.failure_reason ?? 'No Validated Result is available.';
  else actionMessage.textContent = project.state === 'Succeeded' ? 'Validated Result is ready.' : '';

  const events = progressByProject.get(project.project_id) ?? [];
  progressCount.textContent = String(events.length);
  progressList.replaceChildren();
  if (events.length === 0) {
    progressList.innerHTML = '<p class="panel-empty">No Agent Run yet.</p>';
  } else {
    const historical = project.state !== 'Running';
    for (const event of events) {
      const item = document.createElement('article');
      item.className = 'progress-item';
      const heading = document.createElement('div');
      heading.className = 'progress-heading';
      const stage = document.createElement('strong');
      stage.textContent = `${historical ? 'history · ' : ''}${event.stage.replaceAll('_', ' ')}`;
      const time = document.createElement('time');
      time.textContent = formatTime(event.created_at);
      heading.append(stage, time);
      const detail = document.createElement('span');
      detail.textContent = [event.tool, event.attempt ? `attempt ${event.attempt}` : null, event.result].filter(Boolean).join(' · ');
      item.append(heading, detail);
      progressList.append(item);
    }
  }
}

function renderViewerEmpty(): void {
  const project = currentProject();
  const visible = loadedSceneProjectId !== project?.project_id && previewProjectId !== project?.project_id;
  viewerEmpty.hidden = !visible;
  if (!project) {
    viewerEmptyTitle.textContent = 'No Project selected';
    viewerEmptyCopy.textContent = 'Select a Project to inspect its own Scene Artifact.';
  } else if (project.state === 'Draft') {
    viewerEmptyTitle.textContent = 'Draft Project';
    viewerEmptyCopy.textContent = 'Submit a complete Prompt to generate a Validated Result.';
  } else if (project.state === 'Running') {
    viewerEmptyTitle.textContent = 'Agent Run in progress';
    viewerEmptyCopy.textContent = 'The Scene Artifact will appear only after validation succeeds.';
  } else if (project.state === 'Failed') {
    viewerEmptyTitle.textContent = 'No Validated Result';
    viewerEmptyCopy.textContent = 'This Project failed validation. Partial Scene output is hidden.';
  } else if (project.state === 'Stopped') {
    viewerEmptyTitle.textContent = 'Run stopped';
    viewerEmptyCopy.textContent = 'This Project has no validated Scene Artifact.';
  } else {
    viewerEmptyTitle.textContent = 'Loading Validated Result';
    viewerEmptyCopy.textContent = 'Fetching this Project’s canonical Scene Artifact.';
  }
}

function renderAll(): void {
  renderCatalog();
  renderWorkspace();
  renderHarnessSelector();
  renderViewerEmpty();
}

function setMessage(message: string): void {
  workspaceMessage = message;
  renderWorkspace();
}

function closeProgressStream(): void {
  eventSource?.close();
  eventSource = null;
}

function isCurrentPreview(projectId: string, version: number, attempt: number, revision: number): boolean {
  return version === selectedVersion
    && selectedProjectId === projectId
    && currentProject()?.state === 'Running'
    && latestPreviewAttempt === attempt
    && latestPreviewRevision === revision;
}

async function handlePreviewEvent(event: MessageEvent, projectId: string, version: number): Promise<void> {
  if (version !== selectedVersion || selectedProjectId !== projectId || currentProject()?.state !== 'Running') return;
  let controller: AbortController | null = null;
  try {
    const record = JSON.parse(event.data) as ProgressRecord;
    const preview = record.preview;
    if (!preview || !Number.isSafeInteger(preview.attempt) || preview.attempt < 1 || !Number.isSafeInteger(preview.revision) || preview.revision < 1 || typeof preview.operation !== 'string' || !/^[a-z][a-z0-9_]{0,31}$/.test(preview.operation)) return;
    if (preview.attempt !== latestPreviewAttempt) {
      if (preview.attempt < latestPreviewAttempt) return;
      latestPreviewAttempt = preview.attempt;
      latestPreviewRevision = 0;
    }
    if (preview.revision <= latestPreviewRevision) return;
    latestPreviewRevision = preview.revision;
    previewRequest?.abort();
    controller = new AbortController();
    previewRequest = controller;
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/previews/${preview.attempt}/${preview.revision}`,
      {
        signal: controller.signal,
        headers: { Accept: 'model/gltf-binary' },
      },
    );
    if (!response.ok) throw new Error(`Preview request failed (${response.status})`);
    const payload = await response.arrayBuffer();
    if (!isCurrentPreview(projectId, version, preview.attempt, preview.revision)) return;
    const displayed = await sceneViewer.loadPreview(
      payload,
      `${preview.operation} preview`,
      () => isCurrentPreview(projectId, version, preview.attempt, preview.revision),
    );
    if (displayed && isCurrentPreview(projectId, version, preview.attempt, preview.revision)) {
      previewProjectId = projectId;
      renderViewerEmpty();
    }
  } catch (error) {
    if (controller?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return;
    if (version === selectedVersion && selectedProjectId === projectId) setMessage('Live preview could not be displayed.');
  } finally {
    if (controller !== null && previewRequest === controller) previewRequest = null;
  }
}

function openProgressStream(projectId: string, version: number): void {
  closeProgressStream();
  const source = new EventSource(`/api/projects/${encodeURIComponent(projectId)}/events`);
  eventSource = source;
  source.addEventListener('progress', (event) => {
    if (version !== selectedVersion || selectedProjectId !== projectId) return;
    try {
      const record = JSON.parse((event as MessageEvent).data) as ProgressRecord;
      const events = progressByProject.get(projectId) ?? [];
      if (!events.some((item) => item.id === record.id)) events.push(record);
      progressByProject.set(projectId, events);
      renderWorkspace();
      if (['completed', 'failed', 'stopped'].includes(record.stage) && currentProject()?.state === 'Running') {
        void refreshSelectedProject(version);
      }
    } catch {
      setMessage('Progress Event could not be displayed.');
    }
  });
  source.addEventListener('scene-preview', (event) => {
    void handlePreviewEvent(event as MessageEvent, projectId, version);
    try {
      const record = JSON.parse((event as MessageEvent).data) as ProgressRecord;
      const events = progressByProject.get(projectId) ?? [];
      if (!events.some((item) => item.id === record.id)) events.push(record);
      progressByProject.set(projectId, events);
      renderWorkspace();
    } catch {
      setMessage('Progress Event could not be displayed.');
    }
  });
}

async function refreshSelectedProject(version: number): Promise<void> {
  const projectId = selectedProjectId;
  if (!projectId) return;
  try {
    const project = await request<Project>(`/api/projects/${encodeURIComponent(projectId)}`);
    if (version !== selectedVersion || selectedProjectId !== projectId) return;
    upsertProject(project);
    workspaceMessage = '';
    renderAll();
    if (project.state === 'Succeeded' && loadedSceneProjectId !== projectId) await loadScene(project, version);
    if (project.state !== 'Succeeded' && loadedSceneProjectId === projectId) {
      loadedSceneProjectId = null;
      if (project.state === 'Failed' || project.state === 'Stopped') sceneViewer.markPreviewUnvalidated();
      else sceneViewer.clear();
      renderViewerEmpty();
    }
    if (project.state === 'Failed' || project.state === 'Stopped') sceneViewer.markPreviewUnvalidated();
  } catch (error) {
    if (version === selectedVersion) setMessage(errorMessage(error));
  }
}

async function loadScene(project: Project, version: number): Promise<void> {
  if (project.state !== 'Succeeded') return;
  sceneRequest?.abort();
  const controller = new AbortController();
  sceneRequest = controller;
  loadedSceneProjectId = null;
  previewRequest?.abort();
  previewRequest = null;
  previewProjectId = null;
  latestPreviewAttempt = 0;
  latestPreviewRevision = 0;
  sceneViewer.clear('Loading canonical Scene Artifact');
  viewerEmpty.hidden = true;
  viewerLoading.hidden = false;
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(project.project_id)}/scene`, { signal: controller.signal });
    if (!response.ok) throw new Error(`Scene Artifact request failed (${response.status})`);
    const blob = await response.blob();
    if (version !== selectedVersion || selectedProjectId !== project.project_id) return;
    await sceneViewer.load(blob);
    if (version === selectedVersion && selectedProjectId === project.project_id) {
      loadedSceneProjectId = project.project_id;
      renderViewerEmpty();
    }
  } catch (error) {
    if (controller.signal.aborted || version !== selectedVersion) return;
    sceneViewer.clear();
    setMessage(errorMessage(error));
    renderViewerEmpty();
  } finally {
    if (sceneRequest === controller) {
      sceneRequest = null;
      viewerLoading.hidden = true;
    }
  }
}

async function selectProject(projectId: string): Promise<void> {
  if (!projects.some((project) => project.project_id === projectId)) return;
  selectedProjectId = projectId;
  selectedVersion += 1;
  const version = selectedVersion;
  workspaceMessage = '';
  closeProgressStream();
  sceneRequest?.abort();
  previewRequest?.abort();
  previewRequest = null;
  previewProjectId = null;
  latestPreviewAttempt = 0;
  latestPreviewRevision = 0;
  loadedSceneProjectId = null;
  sceneViewer.clear();
  progressByProject.set(projectId, []);
  renderAll();
  try {
    const project = await request<Project>(`/api/projects/${encodeURIComponent(projectId)}`);
    if (version !== selectedVersion) return;
    upsertProject(project);
    renderAll();
    openProgressStream(projectId, version);
    if (project.state === 'Succeeded') await loadScene(project, version);
  } catch (error) {
    if (version === selectedVersion) setMessage(errorMessage(error));
  }
}

async function refreshCatalog(): Promise<void> {
  try {
    projects = await request<Project[]>('/api/projects');
    if (selectedProjectId && !projects.some((project) => project.project_id === selectedProjectId)) {
      closeProgressStream();
      selectedProjectId = null;
      selectedVersion += 1;
      previewRequest?.abort();
      previewRequest = null;
      previewProjectId = null;
      latestPreviewAttempt = 0;
      latestPreviewRevision = 0;
      loadedSceneProjectId = null;
      sceneViewer.clear();
    }
    renderAll();
    if (!selectedProjectId && projects[0]) await selectProject(projects[0].project_id);
  } catch (error) {
    serviceMessage.textContent = errorMessage(error);
  }
}

async function refreshHarnesses(): Promise<void> {
  try {
    harnessStatuses = await request<HarnessStatus[]>('/api/harnesses');
    renderAll();
  } catch (error) {
    serviceMessage.textContent = errorMessage(error);
  }
}

async function createProject(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const input = document.querySelector<HTMLInputElement>('#new-project-name')!;
  const name = input.value.trim();
  if (!name) {
    serviceMessage.textContent = 'Project name must not be empty.';
    input.focus();
    return;
  }
  try {
    const project = await request<Project>('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    input.value = '';
    selectedHarness = 'deepagents';
    upsertProject(project);
    selectedProjectId = project.project_id;
    await refreshCatalog();
    await selectProject(project.project_id);
  } catch (error) {
    serviceMessage.textContent = errorMessage(error);
  }
}

async function submitPrompt(): Promise<void> {
  const project = currentProject();
  const prompt = promptInput.value;
  if (!project || project.state !== 'Draft') return;
  if (!prompt.trim()) {
    setMessage('Prompt must not be empty.');
    promptInput.focus();
    return;
  }
  if (prompt.length > MAX_PROMPT_CHARS) {
    setMessage(`Prompt exceeds the ${MAX_PROMPT_CHARS.toLocaleString()}-character limit.`);
    promptInput.focus();
    return;
  }
  runButton.disabled = true;
  workspaceMessage = '';
  try {
    const updated = await request<Project>(`/api/projects/${encodeURIComponent(project.project_id)}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, harness: selectedHarnessFor(project) }),
    });
    upsertProject(updated);
    renderAll();
    await refreshCatalog();
  } catch (error) {
    setMessage(errorMessage(error));
  }
}

async function stopRun(): Promise<void> {
  const project = currentProject();
  if (!project || project.state !== 'Running') return;
  stopButton.disabled = true;
  try {
    const updated = await request<Project>(`/api/projects/${encodeURIComponent(project.project_id)}/stop`, { method: 'POST' });
    upsertProject(updated);
    workspaceMessage = '';
    renderAll();
    await refreshCatalog();
  } catch (error) {
    setMessage(errorMessage(error));
  }
}

async function deleteProject(): Promise<void> {
  const project = currentProject();
  if (!project) return;
  const confirmed = window.confirm(`Permanently delete “${project.name}”?`);
  if (!confirmed) return;
  try {
    await request<void>(`/api/projects/${encodeURIComponent(project.project_id)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_name: project.name }),
    });
    closeProgressStream();
    sceneRequest?.abort();
    previewRequest?.abort();
    previewRequest = null;
    previewProjectId = null;
    latestPreviewAttempt = 0;
    latestPreviewRevision = 0;
    projects = projects.filter((item) => item.project_id !== project.project_id);
    selectedProjectId = null;
    selectedVersion += 1;
    loadedSceneProjectId = null;
    sceneViewer.clear();
    await refreshCatalog();
  } catch (error) {
    setMessage(errorMessage(error));
  }
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderRunMetrics(project: Project | null): void {
  const terminal = project !== null && ['Succeeded', 'Failed', 'Stopped'].includes(project.state);
  const usage = terminal ? project.token_usage : null;
  setMetric(totalTokens, formatTokenCount(usage?.total_tokens ?? null));
  setMetric(inputTokens, formatTokenCount(usage?.input_tokens ?? null));
  setMetric(cachedTokens, formatTokenCount(usage?.cached_input_tokens ?? null));
  setMetric(uncachedTokens, formatTokenCount(usage?.uncached_input_tokens ?? null));
  setMetric(outputTokens, formatTokenCount(usage?.output_tokens ?? null));
  setMetric(runTime, formatDuration(terminal ? project.duration_seconds : null));
}

function setMetric(element: HTMLElement, value: string): void {
  element.textContent = value;
  element.title = value === '--' ? '' : value;
}

function formatTokenCount(value: number | null): string {
  return isNonNegativeFinite(value) ? Math.trunc(value).toLocaleString() : '--';
}

function formatCompactTokenCount(value: number | null): string {
  if (!isNonNegativeFinite(value)) return '--';
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(Math.trunc(value));
}

function formatDuration(value: number | null): string {
  if (!isNonNegativeFinite(value)) return '--';
  if (value < 60) {
    const seconds = value < 10 ? value.toFixed(1).replace(/\.0$/, '') : String(Math.round(value));
    return `${seconds}s`;
  }
  const totalSeconds = Math.round(value);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

function isNonNegativeFinite(value: number | null): value is number {
  return value !== null && Number.isFinite(value) && value >= 0;
}

function errorMessage(error: unknown): string {
  if (error instanceof ScenePackageError) return error.message;
  return error instanceof Error ? error.message : 'The request could not be completed.';
}

document.querySelector<HTMLFormElement>('#create-project-form')!.addEventListener('submit', (event) => void createProject(event));
document.querySelector<HTMLButtonElement>('#refresh-projects')!.addEventListener('click', () => void refreshCatalog());
runButton.addEventListener('click', () => void submitPrompt());
stopButton.addEventListener('click', () => void stopRun());
deleteButton.addEventListener('click', () => void deleteProject());
document.querySelector<HTMLButtonElement>('#fit-button')!.addEventListener('click', () => sceneViewer.fit());
harnessSelector.addEventListener('click', (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>('[data-harness]');
  const project = currentProject();
  if (!button || !project || project.state !== 'Draft' || globalRunActive() || button.disabled) return;
  selectedHarness = button.dataset.harness as AgentHarness;
  workspaceMessage = '';
  renderAll();
});
promptInput.addEventListener('input', () => {
  promptCounter.textContent = `${promptInput.value.length.toLocaleString()} / ${MAX_PROMPT_CHARS.toLocaleString()}`;
});
promptInput.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault();
    void submitPrompt();
  }
});
window.addEventListener('beforeunload', () => {
  closeProgressStream();
  sceneRequest?.abort();
  previewRequest?.abort();
  sceneViewer.dispose();
});

renderAll();
void refreshCatalog();
void refreshHarnesses();
