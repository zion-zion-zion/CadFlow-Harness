import './trace.css';

type ProjectState = 'Draft' | 'Running' | 'Succeeded' | 'Failed' | 'Stopped';
type TokenUsage = { total_tokens: number | null };
type TraceProject = {
  project_id: string;
  name: string;
  state: ProjectState;
  updated_at: string;
  harness: string;
  duration_seconds: number | null;
  token_usage: TokenUsage | null;
  trace_available: boolean;
  event_count: number;
  trace_bytes: number;
};
type TraceEvent = {
  cursor: number;
  sequence: number | null;
  timestamp: string | null;
  type: string;
  role: string | null;
  title: string;
  summary: string;
  tool_name: string | null;
  call_id: string | null;
  is_error: boolean;
  byte_size: number;
};
type TraceBatch = {
  events: TraceEvent[];
  next_offset: number;
  reset: boolean;
  has_incomplete_tail: boolean;
  project: TraceProject;
  trace: Pick<TraceProject, 'trace_available' | 'event_count' | 'trace_bytes'>;
};
type TraceDetail = { cursor: number; event: Record<string, unknown>; raw: string };
type DisplayEvent = { event: TraceEvent; result?: TraceEvent };
type DetailTab = 'summary' | 'payload' | 'result' | 'raw' | 'timing';
type TrackMode = 'duration' | 'sequence';

const POLL_MS = 3_000;
const ROW_HEIGHT = 58;
const root = document.querySelector<HTMLDivElement>('#trace-app');
if (!root) throw new Error('trace root is missing');

root.innerHTML = `
  <a class="skip-link" href="#main-content">Skip to trace</a>
  <div class="trace-shell">
    <header class="trace-topbar">
      <div class="trace-brand"><span class="trace-brand-mark">CF</span><div><strong>CadFlow Trace</strong><span>AGENT RUN OBSERVABILITY</span></div></div>
      <div class="trace-run-heading"><strong id="active-project-name">No Project selected</strong><code id="active-project-id"></code></div>
      <div class="trace-top-actions">
        <span id="poll-status" class="poll-status" role="status"><span></span>Idle</span>
        <button id="refresh-trace" class="trace-button" type="button">Refresh</button>
        <a id="download-trace" class="trace-button" aria-disabled="true">Download</a>
        <a class="trace-button trace-button-primary" href="/">Workspace</a>
      </div>
    </header>

    <aside class="trace-catalog" aria-label="Trace Projects">
      <div class="catalog-header"><span class="trace-eyebrow">PROJECTS</span><span id="project-count" class="trace-count">0</span></div>
      <label class="search-field catalog-search"><span class="sr-only">Search Projects</span><input id="project-search" type="search" placeholder="Search name or ID" autocomplete="off" /></label>
      <div id="state-filter" class="segmented" role="group" aria-label="Project state filter">
        <button class="selected" type="button" data-state="All" aria-pressed="true">All</button>
        <button type="button" data-state="Running" aria-pressed="false">Running</button>
        <button type="button" data-state="Failed" aria-pressed="false">Failed</button>
      </div>
      <div id="project-list" class="trace-project-list" role="list"></div>
      <div id="project-empty" class="trace-empty" hidden>No matching Projects</div>
    </aside>

    <main class="trace-main" id="main-content">
      <section class="trace-overview" aria-label="Trace overview">
        <div class="metrics-row">
          <dl class="trace-metrics">
            <div><dt>Duration</dt><dd id="metric-duration">--</dd></div>
            <div><dt>Events</dt><dd id="metric-events">--</dd></div>
            <div><dt>Model turns</dt><dd id="metric-turns">--</dd></div>
            <div><dt>Tool calls</dt><dd id="metric-tools">--</dd></div>
            <div><dt>Total tokens</dt><dd id="metric-tokens">--</dd></div>
          </dl>
          <div id="track-mode" class="segmented" role="group" aria-label="Trajectory scale">
            <button class="selected" type="button" data-mode="duration" aria-pressed="true">Duration</button>
            <button type="button" data-mode="sequence" aria-pressed="false">Sequence</button>
          </div>
        </div>
        <div class="trajectory" id="trajectory" aria-label="Agent Run trajectory">
          <div class="track-labels"><span>Input</span><span>Model</span><span>Tools</span><span>Run</span></div>
          <div id="track-lanes" class="track-lanes"></div>
        </div>
      </section>

      <section class="trace-workbench">
        <section class="timeline-pane" aria-label="Event timeline">
          <div class="timeline-toolbar">
            <div class="timeline-tabs" role="tablist" aria-label="Trace view">
              <button id="semantic-view" class="selected" type="button" role="tab" aria-selected="true">Trajectory</button>
              <button id="raw-view" type="button" role="tab" aria-selected="false">Raw events</button>
            </div>
            <label class="search-field trace-search"><span class="sr-only">Search full trace</span><input id="trace-search" type="search" placeholder="Search full trace" autocomplete="off" /></label>
          </div>
          <div class="filter-row">
            <div id="event-filters" class="event-filters" role="group" aria-label="Event filters"></div>
            <button id="live-follow" class="follow-toggle selected" type="button" aria-pressed="true"><span></span>Live follow</button>
          </div>
          <div id="timeline-scroll" class="timeline-scroll" tabindex="0">
            <div id="timeline-spacer" class="timeline-spacer"></div>
            <div id="timeline-empty" class="timeline-empty">Select a Project with an Agent Run trace.</div>
          </div>
          <button id="new-events" class="new-events" type="button" hidden>0 new events</button>
        </section>

        <div id="inspector-resizer" class="inspector-resizer" role="separator" aria-label="Resize inspector" aria-orientation="vertical" tabindex="0"></div>
        <aside class="inspector" aria-label="Event inspector">
          <header class="inspector-heading">
            <div><span id="inspector-kind" class="event-kind kind-run">EVENT</span><strong id="inspector-title">Nothing selected</strong><span id="inspector-hierarchy">Select an event from the trajectory</span></div>
            <button id="copy-detail" class="copy-button" type="button" disabled>Copy</button>
          </header>
          <div id="detail-tabs" class="detail-tabs" role="tablist" aria-label="Event detail"></div>
          <div id="detail-content" class="detail-content"><div class="inspector-empty">Event details appear here.</div></div>
        </aside>
      </section>
    </main>
  </div>`;

const projectList = byId<HTMLDivElement>('project-list');
const projectEmpty = byId<HTMLDivElement>('project-empty');
const projectSearch = byId<HTMLInputElement>('project-search');
const projectCount = byId<HTMLSpanElement>('project-count');
const activeProjectName = byId<HTMLElement>('active-project-name');
const activeProjectId = byId<HTMLElement>('active-project-id');
const pollStatus = byId<HTMLElement>('poll-status');
const refreshButton = byId<HTMLButtonElement>('refresh-trace');
const downloadLink = byId<HTMLAnchorElement>('download-trace');
const timelineScroll = byId<HTMLDivElement>('timeline-scroll');
const timelineSpacer = byId<HTMLDivElement>('timeline-spacer');
const timelineEmpty = byId<HTMLDivElement>('timeline-empty');
const trajectory = byId<HTMLDivElement>('track-lanes');
const traceSearch = byId<HTMLInputElement>('trace-search');
const newEventsButton = byId<HTMLButtonElement>('new-events');
const liveFollowButton = byId<HTMLButtonElement>('live-follow');
const semanticButton = byId<HTMLButtonElement>('semantic-view');
const rawButton = byId<HTMLButtonElement>('raw-view');
const eventFilters = byId<HTMLDivElement>('event-filters');
const detailTabs = byId<HTMLDivElement>('detail-tabs');
const detailContent = byId<HTMLDivElement>('detail-content');
const inspectorKind = byId<HTMLElement>('inspector-kind');
const inspectorTitle = byId<HTMLElement>('inspector-title');
const inspectorHierarchy = byId<HTMLElement>('inspector-hierarchy');
const copyDetail = byId<HTMLButtonElement>('copy-detail');

let projects: TraceProject[] = [];
let selectedProjectId: string | null = projectIdFromPath();
let projectStateFilter = 'All';
let events: TraceEvent[] = [];
let nextOffset = 0;
let rawMode = false;
let trackMode: TrackMode = 'duration';
let selectedCursor: number | null = null;
let selectedDisplay: DisplayEvent | null = null;
let detail: TraceDetail | null = null;
let resultDetail: TraceDetail | null = null;
let detailTab: DetailTab = 'summary';
let activeFilters = new Set<string>();
let liveFollow = true;
let unseenEvents = 0;
let pollTimer: number | null = null;
let searchTimer: number | null = null;
let loadVersion = 0;

const FILTERS = ['Input', 'Model', 'Tools', 'Run', 'Errors'];
for (const label of FILTERS) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.dataset.filter = label;
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => {
    if (activeFilters.has(label)) activeFilters.delete(label); else activeFilters.add(label);
    button.classList.toggle('selected', activeFilters.has(label));
    button.setAttribute('aria-pressed', String(activeFilters.has(label)));
    renderTrace();
  });
  eventFilters.append(button);
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`missing #${id}`);
  return element as T;
}

async function request<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  const text = await response.text();
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = String((JSON.parse(text) as { detail?: unknown }).detail ?? message); } catch { /* keep status */ }
    throw new Error(message);
  }
  return JSON.parse(text) as T;
}

async function loadCatalog(): Promise<void> {
  projects = await request<TraceProject[]>('/api/traces');
  projectCount.textContent = String(projects.length);
  if (!selectedProjectId || !projects.some((project) => project.project_id === selectedProjectId)) {
    selectedProjectId = projects[0]?.project_id ?? null;
    if (selectedProjectId) history.replaceState(null, '', `/trace/${selectedProjectId}`);
  }
  renderCatalog();
}

function renderCatalog(): void {
  const query = projectSearch.value.trim().toLowerCase();
  const visible = projects.filter((project) => {
    const matchesState = projectStateFilter === 'All' || project.state === projectStateFilter;
    const matchesQuery = !query || project.name.toLowerCase().includes(query) || project.project_id.includes(query);
    return matchesState && matchesQuery;
  });
  projectList.replaceChildren();
  projectEmpty.hidden = visible.length > 0;
  for (const project of visible) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'trace-project-row';
    row.classList.toggle('selected', project.project_id === selectedProjectId);
    row.setAttribute('role', 'listitem');
    row.setAttribute('aria-current', String(project.project_id === selectedProjectId));
    row.innerHTML = `<span class="project-row-top"><strong>${escapeHtml(project.name)}</strong><span class="state-text state-${project.state.toLowerCase()}">${escapeHtml(project.state)}</span></span><code>${project.project_id.slice(0, 8)}</code><span class="project-row-meta"><span>${escapeHtml(project.harness)}</span><span>${project.event_count} events</span><time>${relativeTime(project.updated_at)}</time></span>`;
    row.addEventListener('click', () => void selectProject(project.project_id));
    projectList.append(row);
  }
}

async function selectProject(projectId: string): Promise<void> {
  selectedProjectId = projectId;
  history.pushState(null, '', `/trace/${projectId}`);
  renderCatalog();
  await loadInitialTrace();
}

async function loadInitialTrace(): Promise<void> {
  stopPolling();
  events = [];
  nextOffset = 0;
  selectedCursor = null;
  selectedDisplay = null;
  detail = null;
  resultDetail = null;
  renderDetail();
  renderTrace();
  if (!selectedProjectId) return;
  const project = currentProject();
  activeProjectName.textContent = project?.name ?? 'Unknown Project';
  activeProjectId.textContent = selectedProjectId;
  downloadLink.href = `/api/projects/${selectedProjectId}/trace/download`;
  downloadLink.setAttribute('aria-disabled', String(!project?.trace_available));
  const version = ++loadVersion;
  setPollStatus('Loading', 'active');
  try {
    const batch = await fetchTrace(0);
    if (version !== loadVersion) return;
    applyBatch(batch, true);
    setPollStatus(batch.project.state === 'Running' ? 'Live' : batch.project.state, batch.project.state === 'Running' ? 'active' : '');
    schedulePolling(batch.project.state);
  } catch (error) {
    if (version !== loadVersion) return;
    timelineEmpty.hidden = false;
    timelineEmpty.textContent = error instanceof Error ? error.message : 'Trace unavailable';
    setPollStatus('Unavailable', 'error');
  }
}

async function fetchTrace(offset: number): Promise<TraceBatch> {
  if (!selectedProjectId) throw new Error('No Project selected');
  const params = new URLSearchParams({ offset: String(offset) });
  const query = traceSearch.value.trim();
  if (query) params.set('q', query);
  return request<TraceBatch>(`/api/projects/${selectedProjectId}/trace?${params}`);
}

function applyBatch(batch: TraceBatch, replace: boolean): void {
  const wasAtBottom = isAtBottom();
  const previousCount = events.length;
  if (replace || batch.reset) events = batch.events; else events.push(...batch.events);
  nextOffset = batch.next_offset;
  const index = projects.findIndex((project) => project.project_id === batch.project.project_id);
  if (index >= 0) projects[index] = { ...projects[index], ...batch.project, ...batch.trace };
  const added = Math.max(0, events.length - previousCount);
  if (!replace && added && (!liveFollow || !wasAtBottom)) {
    unseenEvents += added;
    newEventsButton.textContent = `${unseenEvents} new event${unseenEvents === 1 ? '' : 's'}`;
    newEventsButton.hidden = false;
  }
  renderCatalog();
  renderTrace();
  if ((replace || added) && liveFollow && (replace || wasAtBottom)) scrollToEnd();
}

async function poll(): Promise<void> {
  if (!selectedProjectId) return;
  try {
    const [batch] = await Promise.all([fetchTrace(nextOffset), loadCatalog()]);
    applyBatch(batch, false);
    setPollStatus('Live', 'active');
    schedulePolling(batch.project.state);
  } catch {
    setPollStatus('Retrying', 'error');
    pollTimer = window.setTimeout(() => void poll(), POLL_MS);
  }
}

function schedulePolling(state: ProjectState): void {
  stopPolling();
  if (state === 'Running') pollTimer = window.setTimeout(() => void poll(), POLL_MS);
}

function stopPolling(): void {
  if (pollTimer !== null) window.clearTimeout(pollTimer);
  pollTimer = null;
}

function displayEvents(): DisplayEvent[] {
  const filtered = events.filter((event) => activeFilters.size === 0 || activeFilters.has(eventGroup(event)) || (event.is_error && activeFilters.has('Errors')));
  if (rawMode) return filtered.map((event) => ({ event }));
  const display: DisplayEvent[] = [];
  const callsById = new Map<string, DisplayEvent>();
  const callsByName = new Map<string, DisplayEvent[]>();
  let firstRequestSeen = false;
  for (const event of filtered) {
    if (event.type === 'model_request') {
      if (firstRequestSeen) continue;
      firstRequestSeen = true;
      display.push({ event: { ...event, title: 'Initial context' } });
      continue;
    }
    if (event.type === 'tool_call') {
      const item = { event };
      display.push(item);
      if (event.call_id) callsById.set(event.call_id, item);
      const queue = callsByName.get(event.tool_name ?? '') ?? [];
      queue.push(item);
      callsByName.set(event.tool_name ?? '', queue);
      continue;
    }
    if (event.type === 'tool_result' || event.type === 'tool_error') {
      let call = event.call_id ? callsById.get(event.call_id) : undefined;
      if (!call) call = callsByName.get(event.tool_name ?? '')?.find((item) => !item.result);
      if (call) { call.result = event; continue; }
    }
    display.push({ event });
  }
  return display;
}

function renderTrace(): void {
  renderMetrics();
  renderTrajectory();
  renderTimelineWindow();
}

function renderMetrics(): void {
  const project = currentProject();
  setText('metric-duration', formatDuration(project?.duration_seconds ?? measuredDuration()));
  setText('metric-events', String(project?.event_count ?? events.length));
  setText('metric-turns', String(events.filter((event) => event.type === 'model_response').length));
  setText('metric-tools', String(events.filter((event) => event.type === 'tool_call').length));
  setText('metric-tokens', formatNumber(project?.token_usage?.total_tokens));
}

function renderTrajectory(): void {
  trajectory.replaceChildren();
  const lanes = ['Input', 'Model', 'Tools', 'Run'];
  const timestampValues = events.map((event) => event.timestamp ? Date.parse(event.timestamp) : Number.NaN).filter(Number.isFinite);
  const start = Math.min(...timestampValues);
  const end = Math.max(...timestampValues);
  const span = Math.max(1, end - start);
  lanes.forEach((lane) => {
    const row = document.createElement('div');
    row.className = 'track-lane';
    events.forEach((event, index) => {
      if (eventGroup(event) !== lane && !(lane === 'Run' && event.is_error)) return;
      const block = document.createElement('button');
      block.type = 'button';
      block.className = `track-block kind-${eventKind(event)}`;
      const time = event.timestamp ? Date.parse(event.timestamp) : start;
      const left = trackMode === 'duration' ? ((time - start) / span) * 100 : (index / Math.max(1, events.length)) * 100;
      block.style.left = `${Math.max(0, Math.min(98.5, left))}%`;
      block.title = `${event.title}: ${event.summary}`;
      block.setAttribute('aria-label', block.title);
      block.addEventListener('click', () => void selectEvent({ event }));
      row.append(block);
    });
    trajectory.append(row);
  });
}

function renderTimelineWindow(): void {
  const list = displayEvents();
  timelineSpacer.style.height = `${list.length * ROW_HEIGHT}px`;
  timelineEmpty.hidden = list.length > 0;
  if (!list.length) timelineEmpty.textContent = traceSearch.value ? 'No events match this search.' : 'No trace events available.';
  timelineSpacer.replaceChildren();
  const start = Math.max(0, Math.floor(timelineScroll.scrollTop / ROW_HEIGHT) - 5);
  const count = Math.ceil(timelineScroll.clientHeight / ROW_HEIGHT) + 10;
  const end = Math.min(list.length, start + count);
  for (let index = start; index < end; index += 1) {
    const item = list[index];
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'timeline-row';
    row.classList.toggle('selected', item.event.cursor === selectedCursor);
    row.classList.toggle('error', item.event.is_error || item.result?.is_error === true);
    row.style.transform = `translateY(${index * ROW_HEIGHT}px)`;
    row.setAttribute('aria-label', `${item.event.title}: ${item.event.summary}`);
    const resultStatus = item.result ? `<span class="result-status ${item.result.is_error ? 'failed' : ''}">${item.result.is_error ? 'Failed' : 'Completed'}</span>` : '';
    row.innerHTML = `<span class="event-kind kind-${eventKind(item.event)}">${escapeHtml(eventLabel(item.event))}</span><span class="event-copy"><strong>${escapeHtml(item.event.title)}</strong><span>${escapeHtml(item.event.summary || 'No content')}</span></span>${resultStatus}<time>${formatTime(item.event.timestamp)}</time>`;
    row.addEventListener('click', () => void selectEvent(item));
    timelineSpacer.append(row);
  }
}

async function selectEvent(item: DisplayEvent): Promise<void> {
  selectedDisplay = item;
  selectedCursor = item.event.cursor;
  detailTab = preferredDetailTab(item);
  detail = null;
  resultDetail = null;
  renderTimelineWindow();
  renderDetail();
  if (!selectedProjectId) return;
  const version = ++loadVersion;
  try {
    const baseRequest = request<TraceDetail>(`/api/projects/${selectedProjectId}/trace/events?cursor=${item.event.cursor}`);
    const resultRequest = item.result ? request<TraceDetail>(`/api/projects/${selectedProjectId}/trace/events?cursor=${item.result.cursor}`) : Promise.resolve(null);
    const [base, result] = await Promise.all([baseRequest, resultRequest]);
    if (version !== loadVersion) return;
    detail = base;
    resultDetail = result;
    renderDetail();
  } catch (error) {
    if (version !== loadVersion) return;
    detailContent.textContent = error instanceof Error ? error.message : 'Unable to load event';
  }
}

function renderDetail(): void {
  if (!selectedDisplay) {
    inspectorKind.textContent = 'EVENT';
    inspectorKind.className = 'event-kind kind-run';
    inspectorTitle.textContent = 'Nothing selected';
    inspectorHierarchy.textContent = 'Select an event from the trajectory';
    detailTabs.replaceChildren();
    detailContent.innerHTML = '<div class="inspector-empty">Event details appear here.</div>';
    copyDetail.disabled = true;
    return;
  }
  const event = selectedDisplay.event;
  inspectorKind.textContent = eventLabel(event);
  inspectorKind.className = `event-kind kind-${eventKind(event)}`;
  inspectorTitle.textContent = event.title;
  inspectorHierarchy.textContent = `Sequence ${event.sequence ?? '--'}  /  ${event.type}`;
  const availableTabs: DetailTab[] = ['summary', 'payload'];
  if (hasResultTab(selectedDisplay)) availableTabs.push('result');
  availableTabs.push('raw', 'timing');
  if (!availableTabs.includes(detailTab)) detailTab = 'summary';
  detailTabs.replaceChildren();
  for (const tab of availableTabs) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = titleCase(tab);
    button.classList.toggle('selected', tab === detailTab);
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', String(tab === detailTab));
    button.addEventListener('click', () => { detailTab = tab; renderDetail(); });
    detailTabs.append(button);
  }
  copyDetail.disabled = !detail;
  if (!detail) {
    detailContent.innerHTML = '<div class="detail-loading"><span></span>Loading event</div>';
    return;
  }
  const content = detailValue(detailTab);
  if (detailTab === 'summary' || detailTab === 'timing') {
    detailContent.replaceChildren(summaryPanel(detailTab));
  } else if (isMessageList(content)) {
    detailContent.replaceChildren(renderMessages(content));
  } else {
    detailContent.replaceChildren(renderDataValue(content));
  }
}

function detailValue(tab: DetailTab): unknown {
  if (!detail || !selectedDisplay) return null;
  const value = detail.event;
  if (tab === 'payload') {
    if ('arguments' in value) return value.arguments;
    if ('messages' in value) return value.messages;
    return value;
  }
  if (tab === 'result') {
    if (resultDetail) return resultDetail.event.result ?? resultDetail.event.error ?? resultDetail.event;
    return value.messages ?? value.result ?? value;
  }
  if (tab === 'raw') return resultDetail ? { event: detail.event, result: resultDetail.event } : detail.event;
  return value;
}

function preferredDetailTab(item: DisplayEvent): DetailTab {
  const type = item.event.type;
  if (type === 'model_request' || type === 'tool_call' || type === 'backend_tool') return 'payload';
  if (hasResultTab(item)) return 'result';
  return 'raw';
}

function hasResultTab(item: DisplayEvent): boolean {
  return item.result !== undefined || [
    'model_response',
    'model_error',
    'provider_retry',
    'tool_result',
    'tool_error',
  ].includes(item.event.type);
}

function isMessageList(value: unknown): value is Array<Record<string, unknown>> {
  return Array.isArray(value) && value.length > 0 && value.every((item) => (
    typeof item === 'object' && item !== null && 'role' in item && 'content' in item
  ));
}

function renderDataValue(value: unknown): HTMLElement {
  const normalized = parseStructuredString(value);
  if (typeof normalized === 'string') {
    const document = renderMessageText(normalized);
    document.classList.add('detail-document');
    return document;
  }
  const pre = document.createElement('pre');
  pre.className = 'json-view';
  pre.innerHTML = highlightJson(normalized);
  return pre;
}

function parseStructuredString(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return value;
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return value;
  }
}

function renderMessages(messages: Array<Record<string, unknown>>): HTMLElement {
  const stream = document.createElement('div');
  stream.className = 'message-stream';
  for (const message of messages) {
    const section = document.createElement('section');
    section.className = 'message-block';
    const heading = document.createElement('header');
    const role = String(message.role ?? 'message').toUpperCase();
    heading.innerHTML = `<span class="message-role role-${escapeHtml(role.toLowerCase())}">${escapeHtml(role)}</span>`;
    if (typeof message.name === 'string' && message.name) {
      const name = document.createElement('code');
      name.textContent = message.name;
      heading.append(name);
    }
    section.append(heading, renderMessageText(messageText(message.content)));
    stream.append(section);
  }
  return stream;
}

function messageText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (typeof part === 'string') return part;
      if (typeof part === 'object' && part !== null && 'text' in part && typeof part.text === 'string') return part.text;
      return JSON.stringify(part, null, 2);
    }).filter(Boolean).join('\n\n');
  }
  return content == null ? '' : JSON.stringify(content, null, 2);
}

function renderMessageText(text: string): HTMLElement {
  const body = document.createElement('div');
  body.className = 'message-body';
  let codeLines: string[] | null = null;
  for (const line of text.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      if (codeLines === null) {
        codeLines = [];
      } else {
        body.append(messageCode(codeLines));
        codeLines = null;
      }
      continue;
    }
    if (codeLines !== null) {
      codeLines.push(line);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const numbered = line.match(/^\s*(\d+\.)\s+(.+)$/);
    const element = document.createElement(heading ? 'h4' : 'p');
    if (heading) {
      element.className = 'message-heading';
      appendInlineText(element, heading[2]);
    } else if (bullet) {
      element.className = 'message-list-item';
      appendInlineText(element, bullet[1]);
    } else if (numbered) {
      element.className = 'message-list-item message-numbered';
      element.dataset.marker = numbered[1];
      appendInlineText(element, numbered[2]);
    } else if (!line.trim()) {
      element.className = 'message-spacer';
    } else {
      appendInlineText(element, line);
    }
    body.append(element);
  }
  if (codeLines !== null) body.append(messageCode(codeLines));
  return body;
}

function appendInlineText(parent: HTMLElement, text: string): void {
  const token = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let cursor = 0;
  for (const match of text.matchAll(token)) {
    const index = match.index ?? 0;
    parent.append(document.createTextNode(text.slice(cursor, index)));
    const value = match[0];
    const element = document.createElement(value.startsWith('`') ? 'code' : 'strong');
    element.textContent = value.startsWith('`') ? value.slice(1, -1) : value.slice(2, -2);
    parent.append(element);
    cursor = index + value.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function messageCode(lines: string[]): HTMLElement {
  const pre = document.createElement('pre');
  pre.className = 'message-code';
  const code = document.createElement('code');
  code.textContent = lines.join('\n');
  pre.append(code);
  return pre;
}

function summaryPanel(tab: DetailTab): HTMLElement {
  const panel = document.createElement('dl');
  panel.className = 'summary-grid';
  const event = selectedDisplay!.event;
  if (tab === 'timing') {
    addSummary(panel, 'Started', formatDateTime(event.timestamp));
    addSummary(panel, 'Duration', pairedDuration(selectedDisplay!));
    addSummary(panel, 'Sequence', String(event.sequence ?? '--'));
    addSummary(panel, 'Byte cursor', String(event.cursor));
    addSummary(panel, 'Record size', formatBytes(event.byte_size));
  } else {
    addSummary(panel, 'Hierarchy', event.type.replaceAll('_', ' '));
    addSummary(panel, 'Status', event.is_error || selectedDisplay!.result?.is_error ? 'Failed' : selectedDisplay!.result ? 'Completed' : 'Recorded');
    if (event.tool_name) addSummary(panel, 'Tool', event.tool_name);
    if (event.call_id) addSummary(panel, 'Call ID', event.call_id);
    addSummary(panel, 'Summary', event.summary || 'No content');
  }
  return panel;
}

function addSummary(panel: HTMLElement, label: string, value: string): void {
  const wrapper = document.createElement('div');
  const term = document.createElement('dt');
  const description = document.createElement('dd');
  term.textContent = label;
  description.textContent = value;
  wrapper.append(term, description);
  panel.append(wrapper);
}

function eventGroup(event: TraceEvent): string {
  if (event.type === 'model_request') return 'Input';
  if (event.type === 'model_response' || event.type === 'model_error' || event.type === 'provider_retry') return 'Model';
  if (event.type.includes('tool')) return 'Tools';
  return 'Run';
}

function eventKind(event: TraceEvent): string {
  if (event.is_error) return 'error';
  return eventGroup(event).toLowerCase();
}

function eventLabel(event: TraceEvent): string {
  if (event.is_error) return 'ERROR';
  if (event.type === 'model_request') return 'INPUT';
  if (event.type === 'model_response') return 'ASSISTANT';
  if (event.type.includes('tool')) return 'TOOL';
  return 'RUN';
}

function currentProject(): TraceProject | null {
  return projects.find((project) => project.project_id === selectedProjectId) ?? null;
}

function measuredDuration(): number | null {
  const timestamps = events.map((event) => event.timestamp ? Date.parse(event.timestamp) : Number.NaN).filter(Number.isFinite);
  if (timestamps.length < 2) return null;
  return (Math.max(...timestamps) - Math.min(...timestamps)) / 1000;
}

function pairedDuration(item: DisplayEvent): string {
  if (!item.event.timestamp || !item.result?.timestamp) return '--';
  return formatDuration((Date.parse(item.result.timestamp) - Date.parse(item.event.timestamp)) / 1000);
}

function setPollStatus(label: string, state: string): void {
  pollStatus.className = `poll-status ${state}`;
  pollStatus.lastChild!.textContent = label;
}

function scrollToEnd(): void {
  requestAnimationFrame(() => { timelineScroll.scrollTop = timelineScroll.scrollHeight; });
  unseenEvents = 0;
  newEventsButton.hidden = true;
}

function isAtBottom(): boolean {
  return timelineScroll.scrollHeight - timelineScroll.scrollTop - timelineScroll.clientHeight < ROW_HEIGHT * 2;
}

function setText(id: string, value: string): void { byId<HTMLElement>(id).textContent = value; }
function formatNumber(value: number | null | undefined): string { return value == null ? '--' : new Intl.NumberFormat().format(value); }
function formatDuration(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--';
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(1)} s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}
function formatBytes(value: number): string { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`; }
function formatTime(value: string | null): string { return value ? new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)) : '--'; }
function formatDateTime(value: string | null): string { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value)) : '--'; }
function relativeTime(value: string): string {
  const seconds = Math.round((Date.parse(value) - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (Math.abs(seconds) < 60) return formatter.format(seconds, 'second');
  if (Math.abs(seconds) < 3600) return formatter.format(Math.round(seconds / 60), 'minute');
  if (Math.abs(seconds) < 86400) return formatter.format(Math.round(seconds / 3600), 'hour');
  return formatter.format(Math.round(seconds / 86400), 'day');
}
function titleCase(value: string): string { return value.charAt(0).toUpperCase() + value.slice(1); }
function projectIdFromPath(): string | null { const match = location.pathname.match(/^\/trace\/([0-9a-f]{32})$/); return match?.[1] ?? null; }
function escapeHtml(value: string): string { const node = document.createElement('span'); node.textContent = value; return node.innerHTML; }
function highlightJson(value: unknown): string {
  const json = JSON.stringify(value, null, 2) ?? 'null';
  const token = /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"\s*:)|("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*")|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  let output = '';
  let cursor = 0;
  for (const match of json.matchAll(token)) {
    const index = match.index ?? 0;
    output += escapeHtml(json.slice(cursor, index));
    const kind = match[1] ? 'json-key' : match[2] ? 'json-string' : match[3] ? 'json-literal' : 'json-number';
    output += `<span class="${kind}">${escapeHtml(match[0])}</span>`;
    cursor = index + match[0].length;
  }
  return output + escapeHtml(json.slice(cursor));
}

timelineScroll.addEventListener('scroll', () => {
  renderTimelineWindow();
  if (!isAtBottom() && liveFollow) {
    liveFollow = false;
    liveFollowButton.classList.remove('selected');
    liveFollowButton.setAttribute('aria-pressed', 'false');
  }
});
timelineScroll.addEventListener('keydown', (event) => {
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
  event.preventDefault();
  const list = displayEvents();
  const current = list.findIndex((item) => item.event.cursor === selectedCursor);
  const next = Math.max(0, Math.min(list.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)));
  if (list[next]) void selectEvent(list[next]);
});
liveFollowButton.addEventListener('click', () => {
  liveFollow = !liveFollow;
  liveFollowButton.classList.toggle('selected', liveFollow);
  liveFollowButton.setAttribute('aria-pressed', String(liveFollow));
  if (liveFollow) scrollToEnd();
});
newEventsButton.addEventListener('click', () => { liveFollow = true; liveFollowButton.classList.add('selected'); liveFollowButton.setAttribute('aria-pressed', 'true'); scrollToEnd(); });
refreshButton.addEventListener('click', async () => {
  refreshButton.disabled = true;
  try { await loadCatalog(); await loadInitialTrace(); } finally { refreshButton.disabled = false; }
});
projectSearch.addEventListener('input', renderCatalog);
traceSearch.addEventListener('input', () => {
  if (searchTimer !== null) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => void loadInitialTrace(), 250);
});
document.querySelectorAll<HTMLButtonElement>('#state-filter button').forEach((button) => button.addEventListener('click', () => {
  projectStateFilter = button.dataset.state ?? 'All';
  document.querySelectorAll<HTMLButtonElement>('#state-filter button').forEach((item) => { const selected = item === button; item.classList.toggle('selected', selected); item.setAttribute('aria-pressed', String(selected)); });
  renderCatalog();
}));
document.querySelectorAll<HTMLButtonElement>('#track-mode button').forEach((button) => button.addEventListener('click', () => {
  trackMode = (button.dataset.mode ?? 'duration') as TrackMode;
  document.querySelectorAll<HTMLButtonElement>('#track-mode button').forEach((item) => { const selected = item === button; item.classList.toggle('selected', selected); item.setAttribute('aria-pressed', String(selected)); });
  renderTrajectory();
}));
semanticButton.addEventListener('click', () => { rawMode = false; semanticButton.classList.add('selected'); rawButton.classList.remove('selected'); semanticButton.setAttribute('aria-selected', 'true'); rawButton.setAttribute('aria-selected', 'false'); renderTrace(); });
rawButton.addEventListener('click', () => { rawMode = true; rawButton.classList.add('selected'); semanticButton.classList.remove('selected'); rawButton.setAttribute('aria-selected', 'true'); semanticButton.setAttribute('aria-selected', 'false'); renderTrace(); });
copyDetail.addEventListener('click', async () => {
  const value = detailTab === 'summary' || detailTab === 'timing' ? detailContent.textContent ?? '' : JSON.stringify(detailValue(detailTab), null, 2);
  await navigator.clipboard.writeText(value);
  copyDetail.textContent = 'Copied';
  window.setTimeout(() => { copyDetail.textContent = 'Copy'; }, 1_500);
});
window.addEventListener('popstate', () => { selectedProjectId = projectIdFromPath(); renderCatalog(); void loadInitialTrace(); });
window.addEventListener('resize', renderTimelineWindow);

const resizer = byId<HTMLDivElement>('inspector-resizer');
resizer.addEventListener('pointerdown', (event) => {
  resizer.setPointerCapture(event.pointerId);
  const move = (moveEvent: PointerEvent) => {
    const width = Math.max(300, Math.min(620, window.innerWidth - moveEvent.clientX));
    document.documentElement.style.setProperty('--inspector-width', `${width}px`);
  };
  const stop = () => { resizer.removeEventListener('pointermove', move); resizer.removeEventListener('pointerup', stop); };
  resizer.addEventListener('pointermove', move);
  resizer.addEventListener('pointerup', stop);
});
resizer.addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  const current = Number.parseInt(getComputedStyle(document.documentElement).getPropertyValue('--inspector-width')) || 380;
  const width = Math.max(300, Math.min(620, current + (event.key === 'ArrowLeft' ? 20 : -20)));
  document.documentElement.style.setProperty('--inspector-width', `${width}px`);
});

void (async () => {
  try {
    await loadCatalog();
    await loadInitialTrace();
  } catch (error) {
    setPollStatus('Service error', 'error');
    projectEmpty.hidden = false;
    projectEmpty.textContent = error instanceof Error ? error.message : 'Unable to load Projects';
  }
})();
