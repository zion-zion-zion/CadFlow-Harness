import './style.css';

import { ScenePackageError, SceneViewer } from './components/scene-viewer';

const MAX_PROMPT_CHARS = 32_000;

type ProjectState = 'Draft' | 'Running' | 'Succeeded' | 'Failed' | 'Stopped';
type Project = {
  project_id: string;
  name: string;
  state: ProjectState;
  created_at: string;
  updated_at: string;
  prompt: string | null;
  failure_reason: string | null;
  scene_available: boolean;
  diagnostics_available: boolean;
};
type ProgressRecord = {
  id: number;
  created_at: string;
  stage: string;
  tool: string | null;
  attempt: number | null;
  result: string | null;
};

const app = document.querySelector<HTMLDivElement>('#app');
if (!app) throw new Error('viewer root is missing');

app.innerHTML = `
  <main class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">CF</span>
        <div><strong>CadFlow</strong><span>local text-to-cad workspace</span></div>
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
            <span id="state-badge" class="state-badge"></span>
          </div>
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
let loadedSceneProjectId: string | null = null;
let workspaceMessage = '';

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
    meta.textContent = project.state;
    meta.className = `project-state state-${project.state.toLowerCase()}`;
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
    progressList.innerHTML = '<p class="panel-empty">No Project selected.</p>';
    return;
  }

  projectName.textContent = project.name;
  projectId.textContent = project.project_id;
  stateBadge.textContent = project.state;
  stateBadge.className = `state-badge state-${project.state.toLowerCase()}`;
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
  const visible = loadedSceneProjectId !== project?.project_id;
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
      sceneViewer.clear();
      renderViewerEmpty();
    }
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
      loadedSceneProjectId = null;
      sceneViewer.clear();
    }
    renderAll();
    if (!selectedProjectId && projects[0]) await selectProject(projects[0].project_id);
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
      body: JSON.stringify({ prompt }),
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
  const confirmation = window.prompt(`Type the Project name to permanently delete “${project.name}”:`);
  if (confirmation === null) return;
  try {
    await request<void>(`/api/projects/${encodeURIComponent(project.project_id)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_name: confirmation }),
    });
    closeProgressStream();
    sceneRequest?.abort();
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
  sceneViewer.dispose();
});

renderAll();
void refreshCatalog();
