import './style.css';

import { ScenePackageError, SceneViewer } from './components/scene-viewer';
import {
  buildProductTree,
  flattenProductTree,
  type ProductMotionJoint,
  type ProductSemanticModel,
  type ProductTreeNode,
} from './product-state';
import { shouldLoadCanonicalScene } from './scene-state';

const MAX_PROMPT_CHARS = 32_000;

type ProjectState = 'Draft' | 'Running' | 'Succeeded' | 'Failed' | 'Stopped';
type AgentHarness = 'deepagents';
type TokenUsage = {
  input_tokens: number | null;
  cached_input_tokens: number | null;
  uncached_input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
};
type LivePreviewState = 'waiting' | 'stale' | 'building' | 'current' | 'failed' | 'paused';
type LivePreviewStatus = {
  state: LivePreviewState;
  revision: number;
  source_hash: string | null;
  updated_at: string | null;
  error: string | null;
  stdout: string | null;
  stderr: string | null;
  artifact_available: boolean;
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
  product_available: boolean;
  result_kind: 'part' | 'assembly' | null;
  product_status: 'Accepted' | null;
  artifact_version: number | null;
  turn_count: number;
  diagnostics_available: boolean;
  duration_seconds: number | null;
  token_usage: TokenUsage | null;
  preview: LivePreviewStatus;
};
type ConversationTurn = {
  turn_id: string;
  sequence: number;
  request_id: string | null;
  retry_of: string | null;
  user_message: string;
  assistant_message: string;
  status: 'running' | 'succeeded' | 'failed' | 'stopped' | 'cancelled';
  created_at: string;
  completed_at: string | null;
  artifact_version: number | null;
  error: string | null;
};
type ConversationResponse = {
  conversation_id: string;
  turns: ConversationTurn[];
  current_artifact_version: number | null;
};
type MessageResponse = {
  turn: ConversationTurn;
  project: Project;
  artifact: { version: number | null; scene_available: boolean };
  duplicate: boolean;
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
type ProductFile = {
  path: string;
  sha256: string;
  size_bytes: number;
  download_url: string;
};
type ProductPart = {
  part_id: string;
  quantity: number;
  component_paths: string[];
  step_path: string;
  sha256: string;
  size_bytes: number;
  download_url: string;
};
type ProductBomItem = {
  part_id: string;
  name: string | null;
  material: string | null;
  quantity: number;
  component_paths: string[];
  step_path: string;
};
type ProductValidationCheck = {
  check_id: string;
  status: 'passed' | 'failed' | 'not_applicable' | string;
  message?: string;
  evidence?: unknown;
};
type ProductValidationReport = {
  status: string;
  checks: ProductValidationCheck[];
  blocking_failures: string[];
};
type ProductResponse = {
  schema_version: 'cadflow-product-api/v1';
  result_kind: 'part' | 'assembly';
  status: 'Accepted';
  manifest_url: string;
  summary: {
    component_count: number;
    leaf_part_count: number;
    unique_part_count: number;
    solid_count: number;
    volume_mm3: number;
  };
  files: Record<string, ProductFile>;
  parts: ProductPart[];
  semantic_model: ProductSemanticModel | null;
  bom: ProductBomItem[];
  assumptions: string[];
  validation_report: ProductValidationReport | null;
};
type ProductTab = 'structure' | 'bom' | 'validation' | 'motion';

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
      </div>
      <div class="topbar-actions">
        <span class="local-badge">LOCAL DEMO</span>
        <a id="trace-link" class="quiet-button topbar-link" href="/trace">Trace</a>
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
          <span>A Project keeps its CAD conversation, Agent turns and validated versions together.</span>
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
          <section class="conversation-section" aria-label="CAD conversation">
            <div class="section-heading conversation-heading"><span class="eyebrow">CONVERSATION</span><span id="conversation-count" class="count">0</span></div>
            <div id="conversation-list" class="conversation-list" aria-live="polite"><p class="panel-empty">No messages yet.</p></div>
            <div class="project-controls">
              <label for="prompt-input">Message</label>
              <textarea id="prompt-input" rows="4" maxlength="32000" placeholder="Describe a part or continue refining the current model..."></textarea>
              <div class="prompt-footer"><span id="prompt-counter">0 / 32000</span><span>Cmd/Ctrl + Enter</span></div>
            </div>
            <div class="control-row">
              <button id="run-button" class="primary-button" type="button">Send</button>
              <button id="stop-button" class="warning-button" type="button" hidden>Stop Run</button>
              <button id="clear-button" class="danger-button clear-button" type="button">Clear Conversation</button>
              <button id="delete-button" class="danger-button" type="button">Delete Project</button>
            </div>
            <p id="action-message" class="action-message" role="status"></p>
          </section>
          <details class="progress-section" aria-label="Agent Run progress">
            <summary class="section-heading"><span class="eyebrow">RUN PROGRESS</span><span id="progress-count" class="count">0</span></summary>
            <div id="progress-list" class="progress-list"><p class="panel-empty">No Agent Run yet.</p></div>
          </details>
        </div>
      </section>

      <section class="viewer-panel" aria-label="CAD Viewer">
        <div class="viewer-heading">
          <div><span class="eyebrow">CAD VIEWER</span><strong id="viewer-title">Validated Result</strong></div>
          <div class="viewer-actions">
            <label id="preview-toggle-control" class="preview-toggle" hidden><input id="preview-toggle" type="checkbox" checked /><span>Live</span></label>
            <button id="preview-retry" class="quiet-button" type="button" hidden>Retry</button>
            <button id="fit-button" class="quiet-button" type="button">Fit</button>
          </div>
        </div>
        <div class="viewer-workspace">
          <div id="viewer-stage" class="viewer-stage">
            <div id="viewer" class="viewer"></div>
            <div id="viewer-loading" class="viewer-loading" hidden><span class="spinner"></span><span>Loading Scene Artifact</span></div>
            <div id="viewer-empty" class="viewer-empty"><span class="empty-mark">◇</span><strong id="viewer-empty-title">No Project selected</strong><span id="viewer-empty-copy">Select a Project to inspect its own Scene Artifact.</span></div>
            <div class="viewer-hint">Drag to rotate · right-drag to pan · scroll to zoom</div>
            <div class="viewer-toolbar" aria-label="Preview controls">
              <button id="viewer-rotate" class="viewer-tool-button is-active" type="button" aria-pressed="true">Auto-Rotate</button>
              <button id="viewer-reset" class="viewer-tool-button" type="button">Reset View</button>
            </div>
            <details id="preview-details" class="preview-details" hidden><summary>Preview diagnostics</summary><pre id="preview-log"></pre></details>
            <div id="viewer-status" class="viewer-status" aria-live="polite"><span id="viewer-status-dot" class="status-dot"></span><span id="viewer-status-text">Waiting for a Validated Result</span></div>
          </div>
          <aside id="product-inspector" class="product-inspector" aria-label="Accepted Product" hidden>
            <div class="product-inspector-heading">
              <div class="product-heading-copy"><span class="eyebrow">ACCEPTED PRODUCT</span><strong id="product-title">Product</strong><span id="product-summary"></span></div>
              <div class="product-heading-actions">
                <span id="product-status" class="product-status">Accepted</span>
                <details id="product-downloads" class="product-downloads">
                  <summary>Files</summary>
                  <div id="product-download-list" class="product-download-list"></div>
                </details>
              </div>
            </div>
            <div class="product-tabs" role="tablist" aria-label="Product details">
              <button class="product-tab" type="button" role="tab" data-product-tab="structure">Structure</button>
              <button class="product-tab" type="button" role="tab" data-product-tab="bom">BOM</button>
              <button class="product-tab" type="button" role="tab" data-product-tab="validation">Validation</button>
              <button class="product-tab" type="button" role="tab" data-product-tab="motion">Motion</button>
            </div>
            <div id="product-pane" class="product-pane" role="tabpanel" tabindex="0"></div>
          </aside>
        </div>
      </section>
    </section>
    <footer class="footer"><span>Trusted localhost demo · generated code runs in a bounded local process</span><span id="service-message"></span></footer>
  </main>`;

const projectListElement = document.querySelector<HTMLDivElement>('#project-list')!;
const traceLink = document.querySelector<HTMLAnchorElement>('#trace-link')!;
const projectCount = document.querySelector<HTMLSpanElement>('#project-count')!;
const catalogEmpty = document.querySelector<HTMLParagraphElement>('#catalog-empty')!;
const projectEmpty = document.querySelector<HTMLDivElement>('#project-empty')!;
const projectContent = document.querySelector<HTMLDivElement>('#project-content')!;
const projectName = document.querySelector<HTMLHeadingElement>('#project-name')!;
const projectId = document.querySelector<HTMLElement>('#project-id')!;
const stateBadge = document.querySelector<HTMLSpanElement>('#state-badge')!;
const harnessMetadata = document.querySelector<HTMLSpanElement>('#harness-metadata')!;
const totalTokens = document.querySelector<HTMLElement>('#total-tokens')!;
const inputTokens = document.querySelector<HTMLElement>('#input-tokens')!;
const cachedTokens = document.querySelector<HTMLElement>('#cached-tokens')!;
const uncachedTokens = document.querySelector<HTMLElement>('#uncached-tokens')!;
const outputTokens = document.querySelector<HTMLElement>('#output-tokens')!;
const runTime = document.querySelector<HTMLElement>('#run-time')!;
const conversationCount = document.querySelector<HTMLSpanElement>('#conversation-count')!;
const conversationList = document.querySelector<HTMLDivElement>('#conversation-list')!;
const promptInput = document.querySelector<HTMLTextAreaElement>('#prompt-input')!;
const promptCounter = document.querySelector<HTMLSpanElement>('#prompt-counter')!;
const runButton = document.querySelector<HTMLButtonElement>('#run-button')!;
const stopButton = document.querySelector<HTMLButtonElement>('#stop-button')!;
const clearButton = document.querySelector<HTMLButtonElement>('#clear-button')!;
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
const viewerRotate = document.querySelector<HTMLButtonElement>('#viewer-rotate')!;
const viewerReset = document.querySelector<HTMLButtonElement>('#viewer-reset')!;
const viewerTitle = document.querySelector<HTMLElement>('#viewer-title')!;
const previewToggleControl = document.querySelector<HTMLLabelElement>('#preview-toggle-control')!;
const previewToggle = document.querySelector<HTMLInputElement>('#preview-toggle')!;
const previewRetry = document.querySelector<HTMLButtonElement>('#preview-retry')!;
const previewDetails = document.querySelector<HTMLDetailsElement>('#preview-details')!;
const previewLog = document.querySelector<HTMLPreElement>('#preview-log')!;
const productInspector = document.querySelector<HTMLElement>('#product-inspector')!;
const productTitle = document.querySelector<HTMLElement>('#product-title')!;
const productSummary = document.querySelector<HTMLElement>('#product-summary')!;
const productStatus = document.querySelector<HTMLElement>('#product-status')!;
const productDownloads = document.querySelector<HTMLDetailsElement>('#product-downloads')!;
const productDownloadList = document.querySelector<HTMLDivElement>('#product-download-list')!;
const productPane = document.querySelector<HTMLDivElement>('#product-pane')!;
const productTabButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-product-tab]'));
const serviceMessage = document.querySelector<HTMLSpanElement>('#service-message')!;

let projects: Project[] = [];
let selectedProjectId: string | null = null;
let selectedVersion = 0;
let progressByProject = new Map<string, ProgressRecord[]>();
let conversationByProject = new Map<string, ConversationTurn[]>();
let eventSource: EventSource | null = null;
let sceneRequest: AbortController | null = null;
let previewRequest: AbortController | null = null;
let productRequest: AbortController | null = null;
let previewProjectId: string | null = null;
let latestPreviewRevision = 0;
let loadedPreviewRevision = 0;
let loadedSceneProjectId: string | null = null;
let loadedSceneArtifactVersion: number | null = null;
let loadedProductProjectId: string | null = null;
let loadedProductArtifactVersion: number | null = null;
let acceptedProduct: ProductResponse | null = null;
let acceptedProductTree: ProductTreeNode | null = null;
let productLoading = false;
let productLoadError = '';
let activeProductTab: ProductTab = 'structure';
let selectedProductNodeKey: string | null = null;
let workspaceMessage = '';

const sceneViewer = new SceneViewer(viewerElement, (message, ready) => {
  viewerStatusText.textContent = message;
  viewerStatusDot.classList.toggle('ready', ready);
}, (nodeId) => {
  const node = acceptedProductTree
    ? flattenProductTree(acceptedProductTree).find((item) => item.sceneNodeId === nodeId)
    : undefined;
  if (node) {
    selectedProductNodeKey = node.key;
    renderProductInspector();
  }
});
sceneViewer.setMotionChangeHandler(updateMotionControls);

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
  traceLink.href = project ? `/trace/${encodeURIComponent(project.project_id)}` : '/trace';
  const hasProject = project !== null;
  projectEmpty.hidden = hasProject;
  projectContent.hidden = !hasProject;
  if (!project) {
    promptInput.value = '';
    promptInput.disabled = true;
    runButton.disabled = true;
    stopButton.hidden = true;
    clearButton.disabled = true;
    deleteButton.disabled = true;
    actionMessage.textContent = '';
    harnessMetadata.textContent = '';
    renderRunMetrics(null);
    conversationCount.textContent = '0';
    conversationList.innerHTML = '<p class="panel-empty">No Project selected.</p>';
    progressList.innerHTML = '<p class="panel-empty">No Project selected.</p>';
    return;
  }

  projectName.textContent = project.name;
  projectId.textContent = project.project_id;
  harnessMetadata.textContent = 'Deep Agents';
  stateBadge.textContent = project.state;
  stateBadge.className = `state-badge state-${project.state.toLowerCase()}`;
  renderRunMetrics(project);
  promptCounter.textContent = `${promptInput.value.length.toLocaleString()} / ${MAX_PROMPT_CHARS.toLocaleString()}`;

  const turns = conversationByProject.get(project.project_id) ?? [];
  renderConversation(turns);
  const anotherRunIsActive = globalRunActive() && project.state !== 'Running';
  const composerDisabled = project.state === 'Running' || anotherRunIsActive;
  promptInput.disabled = composerDisabled;
  runButton.hidden = false;
  runButton.textContent = turns.length === 0 ? 'Start Conversation' : 'Send';
  runButton.disabled = composerDisabled || !promptInput.value.trim();
  stopButton.hidden = project.state !== 'Running';
  stopButton.disabled = project.state !== 'Running';
  clearButton.disabled = project.state === 'Running' || turns.length === 0;
  deleteButton.disabled = false;
  if (workspaceMessage) actionMessage.textContent = workspaceMessage;
  else if (anotherRunIsActive) actionMessage.textContent = 'Another Project has an active Agent turn.';
  else if (project.state === 'Failed' || project.state === 'Stopped') actionMessage.textContent = project.failure_reason ?? 'The last turn did not complete.';
  else actionMessage.textContent = project.state === 'Succeeded' ? 'Ready for the next refinement.' : '';

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

function renderConversation(turns: ConversationTurn[]): void {
  conversationCount.textContent = String(turns.length);
  conversationList.replaceChildren();
  if (turns.length === 0) {
    conversationList.innerHTML = '<p class="panel-empty">No messages yet. Describe the first CAD part below.</p>';
    return;
  }
  for (const turn of turns) {
    const article = document.createElement('article');
    article.className = `conversation-turn turn-${turn.status}`;

    const user = document.createElement('div');
    user.className = 'message message-user';
    const userMeta = document.createElement('div');
    userMeta.className = 'message-meta';
    userMeta.innerHTML = `<strong>You</strong><time>${formatTime(turn.created_at)}</time>`;
    const userBody = document.createElement('p');
    userBody.textContent = turn.user_message;
    user.append(userMeta, userBody);

    const assistant = document.createElement('div');
    assistant.className = 'message message-assistant';
    const assistantMeta = document.createElement('div');
    assistantMeta.className = 'message-meta';
    const assistantName = document.createElement('strong');
    assistantName.textContent = 'CadFlow';
    const turnStatus = document.createElement('span');
    turnStatus.className = `turn-status status-${turn.status}`;
    turnStatus.textContent = turn.status;
    assistantMeta.append(assistantName, turnStatus);
    const assistantBody = document.createElement('p');
    assistantBody.textContent = turn.assistant_message
      || turn.error
      || (turn.status === 'running' ? 'Working on this CAD change...' : 'No response was recorded.');
    assistant.append(assistantMeta, assistantBody);

    if (turn.status === 'failed' || turn.status === 'cancelled') {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'retry-turn quiet-button';
      retry.textContent = 'Retry';
      retry.disabled = globalRunActive();
      retry.addEventListener('click', () => void retryTurn(turn));
      assistant.append(retry);
    }
    article.append(user, assistant);
    conversationList.append(article);
  }
  conversationList.scrollTop = conversationList.scrollHeight;
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
    if (project.preview.state === 'waiting') {
      viewerEmptyTitle.textContent = 'Waiting for model';
      viewerEmptyCopy.textContent = 'No preview source is ready yet.';
    } else if (project.preview.state === 'paused') {
      viewerEmptyTitle.textContent = 'Preview paused';
      viewerEmptyCopy.textContent = 'The current preview is paused.';
    } else if (project.preview.state === 'failed') {
      viewerEmptyTitle.textContent = 'Preview failed';
      viewerEmptyCopy.textContent = project.preview.error ?? 'No usable preview is available.';
    } else {
      viewerEmptyTitle.textContent = 'Building live preview';
      viewerEmptyCopy.textContent = 'The latest model.py result will appear automatically.';
    }
  } else if (project.state === 'Failed') {
    viewerEmptyTitle.textContent = project.scene_available ? 'Loading last validated result' : 'No Validated Result';
    viewerEmptyCopy.textContent = project.scene_available
      ? 'The latest turn failed; the previous validated CAD version is still available.'
      : 'This Project has not produced a validated result yet.';
  } else if (project.state === 'Stopped') {
    viewerEmptyTitle.textContent = project.scene_available ? 'Loading last validated result' : 'Run stopped';
    viewerEmptyCopy.textContent = project.scene_available
      ? 'The stopped turn did not replace the previous validated CAD version.'
      : 'This Project has no validated Scene Artifact.';
  } else {
    viewerEmptyTitle.textContent = 'Loading Validated Result';
    viewerEmptyCopy.textContent = 'Fetching this Project’s canonical Scene Artifact.';
  }
}

function renderViewerChrome(): void {
  const project = currentProject();
  const running = project?.state === 'Running';
  viewerTitle.textContent = running
    ? 'Live Preview'
    : project && ['Failed', 'Stopped'].includes(project.state) && project.scene_available
      ? 'Last Validated Result'
      : 'Validated Result';
  previewToggleControl.hidden = !running;
  previewRetry.hidden = !running || project.preview.state !== 'failed';
  previewToggle.checked = project?.preview.state !== 'paused';
  previewToggle.disabled = !running;
  const diagnostics = project
    ? [project.preview.error, project.preview.stderr, project.preview.stdout].filter(Boolean).join('\n\n')
    : '';
  previewDetails.hidden = diagnostics.length === 0;
  previewLog.textContent = diagnostics;
  if (!project) {
    viewerStatusDot.className = 'status-dot';
    viewerStatusText.textContent = 'Waiting for a Project';
    return;
  }
  if (project.scene_available && project.state !== 'Running') {
    viewerStatusDot.className = `status-dot${loadedSceneProjectId === project.project_id ? ' ready' : ''}`;
    viewerStatusText.textContent = loadedSceneProjectId === project.project_id
      ? `Validated Artifact${project.artifact_version ? ` · v${String(project.artifact_version).padStart(4, '0')}` : ''}`
      : 'Loading Validated Result';
    return;
  }
  viewerStatusDot.className = 'status-dot';
  if (project.state === 'Failed' || project.state === 'Stopped') {
    viewerStatusText.textContent = project.preview.artifact_available
      ? 'Last preview · unvalidated'
      : 'No live preview available';
    viewerStatusDot.classList.toggle('error', project.state === 'Failed');
    return;
  }
  if (project.state === 'Draft') {
    viewerStatusText.textContent = 'Live preview starts with the Agent Run';
    return;
  }
  const labels: Record<LivePreviewState, string> = {
    waiting: 'Waiting for model.py',
    stale: 'Source changed · preview stale',
    building: 'Building live preview',
    current: 'Live preview current · unvalidated',
    failed: project.preview.artifact_available ? 'Preview failed · last result retained' : 'Preview failed',
    paused: 'Live preview paused',
  };
  viewerStatusText.textContent = labels[project.preview.state];
  viewerStatusDot.classList.toggle('ready', project.preview.state === 'current');
  viewerStatusDot.classList.toggle('building', project.preview.state === 'building');
  viewerStatusDot.classList.toggle('error', project.preview.state === 'failed');
}

function renderProductInspector(): void {
  const project = currentProject();
  const visible = project !== null && project.product_available && project.state !== 'Running';
  productInspector.hidden = !visible;
  if (!visible || !project) return;

  productTitle.textContent = project.result_kind === 'assembly' ? 'Assembly' : 'Part';
  productStatus.textContent = project.product_status ?? 'Accepted';
  productSummary.textContent = acceptedProduct
    ? `${acceptedProduct.summary.unique_part_count} unique · ${acceptedProduct.summary.leaf_part_count} instance${acceptedProduct.summary.leaf_part_count === 1 ? '' : 's'}`
    : 'Loading product record';
  renderProductDownloads();

  for (const button of productTabButtons) {
    const selected = button.dataset.productTab === activeProductTab;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  productPane.replaceChildren();
  productPane.setAttribute('aria-label', humanizeIdentifier(activeProductTab));
  if (productLoading && !acceptedProduct) {
    appendProductMessage('Loading accepted product...');
    return;
  }
  if (productLoadError) {
    appendProductMessage(productLoadError, true);
    return;
  }
  if (!acceptedProduct) {
    appendProductMessage('Accepted product data is unavailable.', true);
    return;
  }
  if (activeProductTab === 'structure') renderProductStructure();
  else if (activeProductTab === 'bom') renderProductBom();
  else if (activeProductTab === 'validation') renderProductValidation();
  else renderProductMotion();
}

function renderProductDownloads(): void {
  productDownloadList.replaceChildren();
  if (!acceptedProduct) {
    productDownloads.hidden = true;
    productDownloads.open = false;
    return;
  }
  productDownloads.hidden = false;
  const labels: Record<string, string> = {
    product_step: 'Product STEP',
    scene: 'Scene',
    source_snapshot: 'Source snapshot',
    semantic_model: 'Semantic model',
    bom: 'BOM',
    validation_report: 'Validation report',
    assumptions: 'Assumptions',
  };
  const manifest = document.createElement('a');
  manifest.href = acceptedProduct.manifest_url;
  manifest.download = '';
  manifest.textContent = 'Manifest';
  productDownloadList.append(manifest);
  for (const [role, file] of Object.entries(acceptedProduct.files)) {
    const link = document.createElement('a');
    link.href = file.download_url;
    link.download = '';
    link.textContent = labels[role] ?? humanizeIdentifier(role);
    productDownloadList.append(link);
  }
}

function appendProductMessage(message: string, error = false): void {
  const element = document.createElement('p');
  element.className = `product-message${error ? ' error' : ''}`;
  element.textContent = message;
  productPane.append(element);
}

function renderProductMotion(): void {
  const joints = sceneViewer.motionJoints();
  if (joints.length === 0) {
    appendProductMessage('No interactive revolute joints are available for this product.');
    return;
  }
  const toolbar = document.createElement('div');
  toolbar.className = 'product-toolbar motion-toolbar';
  const play = document.createElement('button');
  play.type = 'button';
  play.className = 'inspector-button';
  play.textContent = sceneViewer.isMotionPlaying() ? 'Pause' : 'Play';
  play.addEventListener('click', () => {
    sceneViewer.setMotionPlaying(!sceneViewer.isMotionPlaying(), joints[0].joint_id);
    renderProductInspector();
  });
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'inspector-button';
  reset.textContent = 'Reset';
  reset.addEventListener('click', () => {
    sceneViewer.setMotionPlaying(false);
    sceneViewer.resetMotion();
    renderProductInspector();
  });
  toolbar.append(play, reset);
  const hint = document.createElement('p');
  hint.className = 'product-message';
  hint.textContent = 'Adjust a joint angle to preview its connected parts and couplings.';
  productPane.append(toolbar, hint);
  joints.forEach((joint) => appendMotionJoint(productPane, joint));
}

function appendMotionJoint(container: HTMLElement, joint: ProductMotionJoint): void {
  const field = document.createElement('label');
  field.className = 'motion-joint';
  const heading = document.createElement('span');
  heading.className = 'motion-joint-heading';
  const name = document.createElement('strong');
  name.textContent = joint.label;
  const value = document.createElement('output');
  value.textContent = `${Math.round(sceneViewer.jointAngle(joint.joint_id) ?? joint.initial_angle_degrees)}°`;
  heading.append(name, value);
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = String(joint.lower_angle_degrees);
  slider.max = String(joint.upper_angle_degrees);
  slider.step = '1';
  slider.value = String(sceneViewer.jointAngle(joint.joint_id) ?? joint.initial_angle_degrees);
  slider.addEventListener('input', () => {
    sceneViewer.setMotionPlaying(false);
    sceneViewer.setJointAngle(joint.joint_id, Number(slider.value));
    updateMotionControls();
  });
  slider.dataset.jointId = joint.joint_id;
  field.dataset.jointId = joint.joint_id;
  field.append(heading, slider);
  container.append(field);
}

function updateMotionControls(): void {
  for (const row of productPane.querySelectorAll<HTMLElement>('.motion-joint')) {
    const jointId = row.dataset.jointId;
    if (!jointId) continue;
    const angle = sceneViewer.jointAngle(jointId);
    if (angle === null) continue;
    const slider = row.querySelector<HTMLInputElement>('input[type="range"]');
    const output = row.querySelector<HTMLOutputElement>('output');
    if (slider) slider.value = String(angle);
    if (output) output.textContent = `${Math.round(angle)}°`;
  }
}

function renderProductStructure(): void {
  if (!acceptedProductTree) {
    appendProductMessage('Semantic product structure is unavailable.', true);
    return;
  }
  const sceneReady = loadedSceneProjectId === selectedProjectId;
  const toolbar = document.createElement('div');
  toolbar.className = 'product-toolbar';
  const count = document.createElement('span');
  count.textContent = `${acceptedProduct?.summary.component_count ?? 0} components`;
  const showAll = document.createElement('button');
  showAll.type = 'button';
  showAll.className = 'inspector-button';
  showAll.textContent = 'Show All';
  showAll.disabled = !sceneReady;
  showAll.addEventListener('click', () => {
    sceneViewer.showAll();
    renderProductInspector();
  });
  toolbar.append(count, showAll);

  const tree = document.createElement('div');
  tree.className = 'product-tree';
  tree.setAttribute('role', 'tree');
  appendProductTreeNode(tree, acceptedProductTree, 1, sceneReady);
  productPane.append(toolbar, tree);
}

function appendProductTreeNode(
  tree: HTMLElement,
  node: ProductTreeNode,
  level: number,
  sceneReady: boolean,
): void {
  const row = document.createElement('div');
  row.className = 'product-tree-row';
  row.classList.toggle('selected', selectedProductNodeKey === node.key);
  row.style.setProperty('--tree-depth', String(level - 1));
  row.setAttribute('role', 'treeitem');
  row.setAttribute('aria-level', String(level));
  row.setAttribute('aria-selected', String(selectedProductNodeKey === node.key));

  const select = document.createElement('button');
  select.type = 'button';
  select.className = 'product-tree-select';
  select.disabled = !sceneReady || !sceneViewer.hasNode(node.sceneNodeId);
  select.title = node.path;
  const kind = document.createElement('span');
  kind.className = `tree-kind tree-kind-${node.itemKind}`;
  kind.textContent = node.itemKind === 'assembly' ? 'A' : 'P';
  const copy = document.createElement('span');
  copy.className = 'tree-copy';
  const label = document.createElement('strong');
  label.textContent = node.label;
  const identity = document.createElement('small');
  identity.textContent = node.itemId;
  copy.append(label, identity);
  select.append(kind, copy);
  select.addEventListener('click', () => {
    selectedProductNodeKey = node.key;
    sceneViewer.selectNode(node.sceneNodeId);
    renderProductInspector();
  });

  const visible = sceneViewer.isNodeVisible(node.sceneNodeId);
  const visibility = document.createElement('button');
  visibility.type = 'button';
  visibility.className = 'tree-action';
  visibility.textContent = visible ? 'Hide' : 'Show';
  visibility.title = `${visible ? 'Hide' : 'Show'} ${node.label}`;
  visibility.disabled = !sceneReady || !sceneViewer.hasNode(node.sceneNodeId);
  visibility.addEventListener('click', () => {
    sceneViewer.setNodeVisible(node.sceneNodeId, !visible);
    if (visible && selectedProductNodeKey !== null
      && (selectedProductNodeKey === node.key || selectedProductNodeKey.startsWith(`${node.key}/`))) {
      selectedProductNodeKey = null;
    }
    renderProductInspector();
  });

  const isolate = document.createElement('button');
  isolate.type = 'button';
  isolate.className = 'tree-action';
  isolate.textContent = 'Only';
  isolate.title = `Isolate ${node.label}`;
  isolate.disabled = !sceneReady || !sceneViewer.hasNode(node.sceneNodeId);
  isolate.addEventListener('click', () => {
    selectedProductNodeKey = node.key;
    sceneViewer.isolateNode(node.sceneNodeId);
    renderProductInspector();
  });
  row.append(select, visibility, isolate);
  tree.append(row);
  for (const child of node.children) appendProductTreeNode(tree, child, level + 1, sceneReady);
}

function renderProductBom(): void {
  if (!acceptedProduct || acceptedProduct.bom.length === 0) {
    appendProductMessage('No BOM items are available.', true);
    return;
  }
  const partDownloads = new Map(acceptedProduct.parts.map((part) => [part.part_id, part.download_url]));
  const scroller = document.createElement('div');
  scroller.className = 'bom-scroller';
  const table = document.createElement('table');
  table.className = 'bom-table';
  const head = document.createElement('thead');
  head.innerHTML = '<tr><th>Qty</th><th>Part</th><th>Material</th><th>STEP</th></tr>';
  const body = document.createElement('tbody');
  for (const item of acceptedProduct.bom) {
    const row = document.createElement('tr');
    const quantity = document.createElement('td');
    quantity.textContent = String(item.quantity);
    const part = document.createElement('td');
    const name = document.createElement('strong');
    name.textContent = item.name || item.part_id;
    const partId = document.createElement('code');
    partId.textContent = item.part_id;
    const instances = document.createElement('details');
    instances.className = 'bom-instances';
    const instanceSummary = document.createElement('summary');
    instanceSummary.textContent = `${item.component_paths.length} path${item.component_paths.length === 1 ? '' : 's'}`;
    const paths = document.createElement('ul');
    for (const componentPath of item.component_paths) {
      const path = document.createElement('li');
      path.textContent = componentPath;
      paths.append(path);
    }
    instances.append(instanceSummary, paths);
    part.append(name, partId, instances);
    const material = document.createElement('td');
    material.textContent = item.material || 'Not specified';
    const downloadCell = document.createElement('td');
    const download = document.createElement('a');
    download.className = 'step-download';
    download.href = partDownloads.get(item.part_id) ?? '#';
    download.download = '';
    download.textContent = 'STEP';
    downloadCell.append(download);
    row.append(quantity, part, material, downloadCell);
    body.append(row);
  }
  table.append(head, body);
  scroller.append(table);
  productPane.append(scroller);
}

function renderProductValidation(): void {
  const report = acceptedProduct?.validation_report;
  if (!report) {
    appendProductMessage('Validation report is unavailable.', true);
    return;
  }
  const summary = document.createElement('div');
  summary.className = 'validation-summary';
  const state = document.createElement('strong');
  state.textContent = report.status;
  const checks = document.createElement('span');
  checks.textContent = `${report.checks.length} checks · ${report.blocking_failures.length} blocking`;
  summary.append(state, checks);

  const list = document.createElement('div');
  list.className = 'validation-list';
  for (const check of report.checks) {
    const detail = document.createElement('details');
    detail.className = `validation-check validation-${check.status}`;
    const heading = document.createElement('summary');
    const status = document.createElement('span');
    status.className = 'validation-check-status';
    status.textContent = check.status === 'not_applicable' ? 'N/A' : check.status;
    const name = document.createElement('strong');
    name.textContent = humanizeIdentifier(check.check_id);
    heading.append(status, name);
    detail.append(heading);
    if (check.message || check.evidence !== undefined) {
      const evidence = document.createElement('pre');
      evidence.textContent = [
        check.message,
        check.evidence === undefined ? null : JSON.stringify(check.evidence, null, 2),
      ].filter((item): item is string => typeof item === 'string' && item.length > 0).join('\n\n');
      detail.append(evidence);
    }
    list.append(detail);
  }
  productPane.append(summary, list);

  const assumptionsHeading = document.createElement('h2');
  assumptionsHeading.className = 'inspector-subheading';
  assumptionsHeading.textContent = 'Assumptions';
  const assumptions = document.createElement('ul');
  assumptions.className = 'assumption-list';
  for (const assumption of acceptedProduct?.assumptions ?? []) {
    const item = document.createElement('li');
    item.textContent = assumption;
    assumptions.append(item);
  }
  if (assumptions.children.length === 0) {
    const item = document.createElement('li');
    item.textContent = 'None recorded';
    assumptions.append(item);
  }
  productPane.append(assumptionsHeading, assumptions);
}

function humanizeIdentifier(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

function renderAll(): void {
  renderCatalog();
  renderWorkspace();
  renderViewerEmpty();
  renderViewerChrome();
  renderProductInspector();
}

function setMessage(message: string): void {
  workspaceMessage = message;
  renderWorkspace();
}

function closeProgressStream(): void {
  eventSource?.close();
  eventSource = null;
}

function resetProductState(resetTab = false): void {
  productRequest?.abort();
  productRequest = null;
  loadedProductProjectId = null;
  loadedProductArtifactVersion = null;
  acceptedProduct = null;
  acceptedProductTree = null;
  productLoading = false;
  productLoadError = '';
  selectedProductNodeKey = null;
  sceneViewer.setMotionModel(null);
  productDownloads.open = false;
  if (resetTab) activeProductTab = 'structure';
}

function shouldLoadProduct(project: Project): boolean {
  return project.product_available
    && project.state !== 'Running'
    && (
      loadedProductProjectId !== project.project_id
      || loadedProductArtifactVersion !== project.artifact_version
    );
}

async function loadProduct(project: Project, version: number): Promise<void> {
  if (!project.product_available || project.state === 'Running') return;
  productRequest?.abort();
  const controller = new AbortController();
  productRequest = controller;
  loadedProductProjectId = null;
  loadedProductArtifactVersion = null;
  acceptedProduct = null;
  acceptedProductTree = null;
  selectedProductNodeKey = null;
  productLoadError = '';
  productLoading = true;
  renderProductInspector();
  try {
    const product = await request<ProductResponse>(
      `/api/projects/${encodeURIComponent(project.project_id)}/product`,
      { signal: controller.signal },
    );
    if (version !== selectedVersion || selectedProjectId !== project.project_id) return;
    if (product.schema_version !== 'cadflow-product-api/v1' || product.status !== 'Accepted') {
      throw new Error('Product API returned an unsupported record.');
    }
    if (!product.semantic_model) throw new Error('Accepted product has no semantic model.');
    acceptedProductTree = buildProductTree(product.semantic_model);
    acceptedProduct = product;
    sceneViewer.setMotionModel(product.semantic_model);
    loadedProductProjectId = project.project_id;
    loadedProductArtifactVersion = project.artifact_version;
  } catch (error) {
    if (controller.signal.aborted || version !== selectedVersion) return;
    productLoadError = errorMessage(error);
  } finally {
    if (productRequest === controller) productRequest = null;
    if (version === selectedVersion && selectedProjectId === project.project_id) {
      productLoading = false;
      renderProductInspector();
    }
  }
}

function isCurrentPreview(projectId: string, version: number, revision: number): boolean {
  const project = currentProject();
  return version === selectedVersion
    && selectedProjectId === projectId
    && project !== null
    && project.state !== 'Draft'
    && project.state !== 'Succeeded'
    && project.preview.revision === revision
    && latestPreviewRevision === revision;
}

async function loadLivePreview(project: Project, version: number): Promise<void> {
  if (!project.preview.artifact_available || project.preview.revision < 1) return;
  const revision = project.preview.revision;
  if (previewProjectId === project.project_id && loadedPreviewRevision === revision) return;
  let controller: AbortController | null = null;
  try {
    previewRequest?.abort();
    controller = new AbortController();
    previewRequest = controller;
    latestPreviewRevision = revision;
    const response = await fetch(`/api/projects/${encodeURIComponent(project.project_id)}/preview`, {
      signal: controller.signal,
      headers: { Accept: 'model/gltf-binary' },
    });
    if (!response.ok) throw new Error(`Preview request failed (${response.status})`);
    const payload = await response.arrayBuffer();
    if (!isCurrentPreview(project.project_id, version, revision)) return;
    const displayed = await sceneViewer.loadPreview(
      payload,
      project.state === 'Running' ? 'Live preview' : 'Last preview',
      () => isCurrentPreview(project.project_id, version, revision),
    );
    if (displayed && isCurrentPreview(project.project_id, version, revision)) {
      previewProjectId = project.project_id;
      loadedPreviewRevision = revision;
      renderViewerEmpty();
      renderViewerChrome();
    }
  } catch (error) {
    if (controller?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return;
    if (version === selectedVersion && selectedProjectId === project.project_id) setMessage('Live preview could not be displayed.');
  } finally {
    if (controller !== null && previewRequest === controller) previewRequest = null;
  }
}

async function refreshLivePreviewStatus(projectId: string, version: number): Promise<void> {
  if (version !== selectedVersion || selectedProjectId !== projectId) return;
  try {
    const status = await request<LivePreviewStatus>(`/api/projects/${encodeURIComponent(projectId)}/preview/status`);
    const project = currentProject();
    if (!project || project.project_id !== projectId || version !== selectedVersion) return;
    project.preview = status;
    renderViewerEmpty();
    renderViewerChrome();
    await loadLivePreview(project, version);
  } catch (error) {
    if (version === selectedVersion && selectedProjectId === projectId) setMessage(errorMessage(error));
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
  source.addEventListener('scene-preview', () => {
    void refreshLivePreviewStatus(projectId, version);
  });
  source.addEventListener('preview-status', () => void refreshLivePreviewStatus(projectId, version));
}

async function refreshSelectedProject(version: number): Promise<void> {
  const projectId = selectedProjectId;
  if (!projectId) return;
  try {
    const [project, conversation] = await Promise.all([
      request<Project>(`/api/projects/${encodeURIComponent(projectId)}`),
      request<ConversationResponse>(`/api/projects/${encodeURIComponent(projectId)}/messages`),
    ]);
    if (version !== selectedVersion || selectedProjectId !== projectId) return;
    upsertProject(project);
    conversationByProject.set(projectId, conversation.turns);
    if (!project.product_available || project.state === 'Running') resetProductState();
    workspaceMessage = '';
    renderAll();
    const artifactLoads: Promise<void>[] = [];
    if (shouldLoadCanonicalScene({
      projectId,
      state: project.state,
      sceneAvailable: project.scene_available,
      artifactVersion: project.artifact_version,
      loadedSceneProjectId,
      loadedSceneArtifactVersion,
      previewProjectId,
    })) artifactLoads.push(loadScene(project, version));
    if (shouldLoadProduct(project)) artifactLoads.push(loadProduct(project, version));
    if (artifactLoads.length > 0) await Promise.all(artifactLoads);
    if ((!project.scene_available || project.state === 'Running') && loadedSceneProjectId === projectId) {
      loadedSceneProjectId = null;
      loadedSceneArtifactVersion = null;
      if (project.state === 'Failed' || project.state === 'Stopped') sceneViewer.markPreviewUnvalidated();
      else sceneViewer.clear();
      renderViewerEmpty();
      renderProductInspector();
    }
    if ((project.state === 'Failed' || project.state === 'Stopped') && !project.scene_available) {
      sceneViewer.markPreviewUnvalidated();
      await loadLivePreview(project, version);
    } else if (project.state === 'Running') {
      await loadLivePreview(project, version);
    }
  } catch (error) {
    if (version === selectedVersion) setMessage(errorMessage(error));
  }
}

async function loadScene(project: Project, version: number): Promise<void> {
  if (!project.scene_available || project.state === 'Running') return;
  sceneRequest?.abort();
  const controller = new AbortController();
  sceneRequest = controller;
  loadedSceneProjectId = null;
  loadedSceneArtifactVersion = null;
  previewRequest?.abort();
  previewRequest = null;
  previewProjectId = null;
  latestPreviewRevision = 0;
  loadedPreviewRevision = 0;
  sceneViewer.clear('Loading canonical Scene Artifact');
  renderProductInspector();
  viewerEmpty.hidden = true;
  viewerLoading.hidden = false;
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(project.project_id)}/scene`, { signal: controller.signal });
    if (!response.ok) throw new Error(`Scene Artifact request failed (${response.status})`);
    const blob = await response.blob();
    if (version !== selectedVersion || selectedProjectId !== project.project_id) return;
    await sceneViewer.load(blob);
    if (acceptedProduct?.semantic_model) sceneViewer.setMotionModel(acceptedProduct.semantic_model);
    if (version === selectedVersion && selectedProjectId === project.project_id) {
      loadedSceneProjectId = project.project_id;
      loadedSceneArtifactVersion = project.artifact_version;
      renderViewerEmpty();
      renderViewerChrome();
      renderProductInspector();
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
      renderProductInspector();
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
  latestPreviewRevision = 0;
  loadedPreviewRevision = 0;
  loadedSceneProjectId = null;
  loadedSceneArtifactVersion = null;
  resetProductState(true);
  promptInput.value = '';
  sceneViewer.clear();
  progressByProject.set(projectId, []);
  renderAll();
  try {
    const [project, conversation] = await Promise.all([
      request<Project>(`/api/projects/${encodeURIComponent(projectId)}`),
      request<ConversationResponse>(`/api/projects/${encodeURIComponent(projectId)}/messages`),
    ]);
    if (version !== selectedVersion) return;
    upsertProject(project);
    conversationByProject.set(projectId, conversation.turns);
    renderAll();
    openProgressStream(projectId, version);
    if (project.scene_available && project.state !== 'Running') {
      const artifactLoads: Promise<void>[] = [loadScene(project, version)];
      if (shouldLoadProduct(project)) artifactLoads.push(loadProduct(project, version));
      await Promise.all(artifactLoads);
    } else {
      await loadLivePreview(project, version);
    }
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
      latestPreviewRevision = 0;
      loadedPreviewRevision = 0;
      loadedSceneProjectId = null;
      loadedSceneArtifactVersion = null;
      resetProductState(true);
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

async function submitMessage(messageOverride?: string, retryOf?: string): Promise<void> {
  const project = currentProject();
  const message = messageOverride ?? promptInput.value;
  if (!project || project.state === 'Running' || globalRunActive()) return;
  if (!message.trim()) {
    setMessage('Message must not be empty.');
    promptInput.focus();
    return;
  }
  if (message.length > MAX_PROMPT_CHARS) {
    setMessage(`Message exceeds the ${MAX_PROMPT_CHARS.toLocaleString()}-character limit.`);
    promptInput.focus();
    return;
  }
  const requestId = createRequestId();
  runButton.disabled = true;
  workspaceMessage = '';
  project.state = 'Running';
  const optimistic: ConversationTurn = {
    turn_id: requestId,
    sequence: (conversationByProject.get(project.project_id)?.length ?? 0) + 1,
    request_id: requestId,
    retry_of: retryOf ?? null,
    user_message: message.trim(),
    assistant_message: '',
    status: 'running',
    created_at: new Date().toISOString(),
    completed_at: null,
    artifact_version: null,
    error: null,
  };
  conversationByProject.set(project.project_id, [
    ...(conversationByProject.get(project.project_id) ?? []),
    optimistic,
  ]);
  if (messageOverride === undefined) promptInput.value = '';
  renderAll();
  try {
    const result = await request<MessageResponse>(`/api/projects/${encodeURIComponent(project.project_id)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: message.trim(),
        request_id: requestId,
        retry_of: retryOf,
      }),
    });
    upsertProject(result.project);
    await refreshConversation(project.project_id);
    workspaceMessage = result.turn.status === 'succeeded'
      ? 'CAD turn completed. You can continue refining the model.'
      : result.turn.error ?? 'The CAD turn did not complete.';
    renderAll();
    await refreshCatalog();
    await refreshSelectedProject(selectedVersion);
  } catch (error) {
    setMessage(errorMessage(error));
    await refreshSelectedProject(selectedVersion);
  }
}

async function retryTurn(turn: ConversationTurn): Promise<void> {
  await submitMessage(turn.user_message, turn.turn_id);
}

async function refreshConversation(projectId: string): Promise<void> {
  const conversation = await request<ConversationResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/messages`,
  );
  if (selectedProjectId === projectId) conversationByProject.set(projectId, conversation.turns);
}

function createRequestId(): string {
  return typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
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

async function setLivePreviewPaused(): Promise<void> {
  const project = currentProject();
  if (!project || project.state !== 'Running') return;
  previewToggle.disabled = true;
  try {
    const status = await request<LivePreviewStatus>(
      `/api/projects/${encodeURIComponent(project.project_id)}/preview/pause`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused: !previewToggle.checked }),
      },
    );
    project.preview = status;
    renderViewerChrome();
  } catch (error) {
    previewToggle.checked = project.preview.state !== 'paused';
    setMessage(errorMessage(error));
  } finally {
    previewToggle.disabled = false;
  }
}

async function retryLivePreview(): Promise<void> {
  const project = currentProject();
  if (!project || project.state !== 'Running') return;
  previewRetry.disabled = true;
  try {
    await request<{ accepted: boolean }>(
      `/api/projects/${encodeURIComponent(project.project_id)}/preview/retry`,
      { method: 'POST' },
    );
  } catch (error) {
    setMessage(errorMessage(error));
  } finally {
    previewRetry.disabled = false;
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
    latestPreviewRevision = 0;
    loadedPreviewRevision = 0;
    loadedSceneArtifactVersion = null;
    resetProductState(true);
    projects = projects.filter((item) => item.project_id !== project.project_id);
    conversationByProject.delete(project.project_id);
    progressByProject.delete(project.project_id);
    selectedProjectId = null;
    selectedVersion += 1;
    loadedSceneProjectId = null;
    sceneViewer.clear();
    await refreshCatalog();
  } catch (error) {
    setMessage(errorMessage(error));
  }
}

async function clearConversation(): Promise<void> {
  const project = currentProject();
  if (!project || project.state === 'Running') return;
  const confirmed = window.confirm(
    `Permanently clear the conversation and every CAD artifact in “${project.name}”?`,
  );
  if (!confirmed) return;
  clearButton.disabled = true;
  try {
    const reset = await request<Project>(
      `/api/projects/${encodeURIComponent(project.project_id)}/conversation`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_name: project.name }),
      },
    );
    conversationByProject.set(project.project_id, []);
    progressByProject.set(project.project_id, []);
    upsertProject(reset);
    promptInput.value = '';
    loadedSceneProjectId = null;
    loadedSceneArtifactVersion = null;
    previewProjectId = null;
    resetProductState(true);
    sceneViewer.clear();
    workspaceMessage = 'Conversation and CAD artifacts were cleared.';
    renderAll();
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

for (const [index, button] of productTabButtons.entries()) {
  button.addEventListener('click', () => {
    activeProductTab = button.dataset.productTab as ProductTab;
    renderProductInspector();
  });
  button.addEventListener('keydown', (event) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % productTabButtons.length;
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + productTabButtons.length) % productTabButtons.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = productTabButtons.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = productTabButtons[nextIndex];
    activeProductTab = next.dataset.productTab as ProductTab;
    renderProductInspector();
    next.focus();
  });
}
document.querySelector<HTMLFormElement>('#create-project-form')!.addEventListener('submit', (event) => void createProject(event));
document.querySelector<HTMLButtonElement>('#refresh-projects')!.addEventListener('click', () => void refreshCatalog());
runButton.addEventListener('click', () => void submitMessage());
stopButton.addEventListener('click', () => void stopRun());
clearButton.addEventListener('click', () => void clearConversation());
deleteButton.addEventListener('click', () => void deleteProject());
previewToggle.addEventListener('change', () => void setLivePreviewPaused());
previewRetry.addEventListener('click', () => void retryLivePreview());
document.querySelector<HTMLButtonElement>('#fit-button')!.addEventListener('click', () => sceneViewer.fit());
viewerRotate.addEventListener('click', () => {
  const enabled = !sceneViewer.isAutoRotate();
  sceneViewer.setAutoRotate(enabled);
  viewerRotate.classList.toggle('is-active', enabled);
  viewerRotate.setAttribute('aria-pressed', String(enabled));
});
viewerReset.addEventListener('click', () => sceneViewer.resetView());
promptInput.addEventListener('input', () => {
  promptCounter.textContent = `${promptInput.value.length.toLocaleString()} / ${MAX_PROMPT_CHARS.toLocaleString()}`;
  const project = currentProject();
  runButton.disabled = !project
    || project.state === 'Running'
    || globalRunActive()
    || !promptInput.value.trim();
});
promptInput.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault();
    void submitMessage();
  }
});
window.addEventListener('beforeunload', () => {
  closeProgressStream();
  sceneRequest?.abort();
  previewRequest?.abort();
  productRequest?.abort();
  sceneViewer.dispose();
});

renderAll();
void refreshCatalog();
