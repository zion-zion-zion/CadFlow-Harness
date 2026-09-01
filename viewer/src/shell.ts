export type ShellElements = {
  app: HTMLDivElement;
  projectList: HTMLDivElement;
  traceLink: HTMLAnchorElement;
  projectCount: HTMLSpanElement;
  catalogEmpty: HTMLParagraphElement;
  projectEmpty: HTMLDivElement;
  projectContent: HTMLDivElement;
  projectName: HTMLHeadingElement;
  projectId: HTMLElement;
  stateBadge: HTMLSpanElement;
  harnessMetadata: HTMLSpanElement;
  totalTokens: HTMLElement;
  inputTokens: HTMLElement;
  cachedTokens: HTMLElement;
  uncachedTokens: HTMLElement;
  outputTokens: HTMLElement;
  runTime: HTMLElement;
  conversationCount: HTMLSpanElement;
  conversationList: HTMLDivElement;
  promptInput: HTMLTextAreaElement;
  promptCounter: HTMLSpanElement;
  runButton: HTMLButtonElement;
  stopButton: HTMLButtonElement;
  clearButton: HTMLButtonElement;
  deleteButton: HTMLButtonElement;
  actionMessage: HTMLParagraphElement;
  progressCount: HTMLSpanElement;
  progressList: HTMLDivElement;
  viewerElement: HTMLDivElement;
  viewerLoading: HTMLDivElement;
  viewerEmpty: HTMLDivElement;
  viewerEmptyTitle: HTMLElement;
  viewerEmptyCopy: HTMLElement;
  viewerStatusText: HTMLSpanElement;
  viewerStatusDot: HTMLSpanElement;
  viewerRotate: HTMLButtonElement;
  viewerReset: HTMLButtonElement;
  viewerTitle: HTMLElement;
  previewToggleControl: HTMLLabelElement;
  previewToggle: HTMLInputElement;
  previewRetry: HTMLButtonElement;
  previewDetails: HTMLDetailsElement;
  previewLog: HTMLPreElement;
  productInspector: HTMLElement;
  productTitle: HTMLElement;
  productSummary: HTMLElement;
  productStatus: HTMLElement;
  productDownloads: HTMLDetailsElement;
  productDownloadList: HTMLDivElement;
  productPane: HTMLDivElement;
  productTabButtons: HTMLButtonElement[];
  serviceMessage: HTMLSpanElement;
  createProjectForm: HTMLFormElement;
  refreshProjects: HTMLButtonElement;
  fitButton: HTMLButtonElement;
};

export function createShell(): ShellElements {
  const app = document.querySelector<HTMLDivElement>('#app');
  if (!app) throw new Error('viewer root is missing');
  app.innerHTML = `
  <main class="shell">
    <header class="topbar">
      <div class="topbar-leading"><div class="brand"><span class="brand-mark">CF</span><div><strong>CadFlow Harness</strong><span>local text-to-cad workspace</span></div></div></div>
      <div class="topbar-actions"><span class="local-badge">LOCAL DEMO</span><a id="trace-link" class="quiet-button topbar-link" href="/trace">Trace</a><button id="refresh-projects" class="quiet-button" type="button">Refresh</button></div>
    </header>
    <section class="workspace">
      <aside class="catalog-panel panel" aria-label="Project Catalog">
        <div class="panel-heading"><div><span class="eyebrow">PROJECT CATALOG</span><h1>Projects</h1></div><span id="project-count" class="count">0</span></div>
        <form id="create-project-form" class="create-project-form"><label for="new-project-name">New Project</label><div class="inline-form"><input id="new-project-name" type="text" maxlength="160" placeholder="Project name" autocomplete="off" /><button class="primary-icon-button" type="submit" aria-label="Create Project">+</button></div></form>
        <div id="project-list" class="project-list" role="list"></div><p id="catalog-empty" class="panel-empty">No Projects yet. Create a Draft Project to begin.</p>
      </aside>
      <section class="project-panel panel" aria-label="Current Project">
        <div id="project-empty" class="empty-workspace"><span class="empty-mark">＋</span><strong>Create or select a Project</strong><span>A Project keeps its CAD conversation, Agent turns and validated versions together.</span></div>
        <div id="project-content" hidden>
          <div class="panel-heading current-heading"><div><span class="eyebrow">CURRENT PROJECT</span><h1 id="project-name"></h1><code id="project-id"></code></div><div class="project-heading-meta"><span id="harness-metadata" class="harness-metadata"></span><span id="state-badge" class="state-badge"></span></div></div>
          <dl class="run-metrics" aria-label="Agent Run metrics"><div><dt>Total Tokens</dt><dd id="total-tokens">--</dd></div><div><dt>Input Tokens</dt><dd id="input-tokens">--</dd></div><div><dt>Cached Tokens</dt><dd id="cached-tokens">--</dd></div><div><dt>Uncached Tokens</dt><dd id="uncached-tokens">--</dd></div><div><dt>Output Tokens</dt><dd id="output-tokens">--</dd></div><div><dt>Total Time</dt><dd id="run-time">--</dd></div></dl>
          <section class="conversation-section" aria-label="CAD conversation"><div class="section-heading conversation-heading"><span class="eyebrow">CONVERSATION</span><span id="conversation-count" class="count">0</span></div><div id="conversation-list" class="conversation-list" aria-live="polite"><p class="panel-empty">No messages yet.</p></div><div class="project-controls"><label for="prompt-input">Message</label><textarea id="prompt-input" rows="4" maxlength="32000" placeholder="Describe a part or continue refining the current model..."></textarea><div class="prompt-footer"><span id="prompt-counter">0 / 32000</span><span>Cmd/Ctrl + Enter</span></div></div><div class="control-row"><button id="run-button" class="primary-button" type="button">Send</button><button id="stop-button" class="warning-button" type="button" hidden>Stop Run</button><button id="clear-button" class="danger-button clear-button" type="button">Clear Conversation</button><button id="delete-button" class="danger-button" type="button">Delete Project</button></div><p id="action-message" class="action-message" role="status"></p></section>
          <details class="progress-section" aria-label="Agent Run progress"><summary class="section-heading"><span class="eyebrow">RUN PROGRESS</span><span id="progress-count" class="count">0</span></summary><div id="progress-list" class="progress-list"><p class="panel-empty">No Agent Run yet.</p></div></details>
        </div>
      </section>
      <section class="viewer-panel" aria-label="CAD Viewer"><div class="viewer-heading"><div><span class="eyebrow">CAD VIEWER</span><strong id="viewer-title">Validated Result</strong></div><div class="viewer-actions"><label id="preview-toggle-control" class="preview-toggle" hidden><input id="preview-toggle" type="checkbox" checked /><span>Live</span></label><button id="preview-retry" class="quiet-button" type="button" hidden>Retry</button><button id="fit-button" class="quiet-button" type="button">Fit</button></div></div>
        <div class="viewer-workspace"><div id="viewer-stage" class="viewer-stage"><div id="viewer" class="viewer"></div><div id="viewer-loading" class="viewer-loading" hidden><span class="spinner"></span><span>Loading Scene Artifact</span></div><div id="viewer-empty" class="viewer-empty"><span class="empty-mark">◇</span><strong id="viewer-empty-title">No Project selected</strong><span id="viewer-empty-copy">Select a Project to inspect its own Scene Artifact.</span></div><div class="viewer-hint">Drag to rotate · right-drag to pan · scroll to zoom</div><div class="viewer-toolbar" aria-label="Preview controls"><button id="viewer-rotate" class="viewer-tool-button is-active" type="button" aria-pressed="true">Auto-Rotate</button><button id="viewer-reset" class="viewer-tool-button" type="button">Reset View</button></div><details id="preview-details" class="preview-details" hidden><summary>Preview diagnostics</summary><pre id="preview-log"></pre></details><div id="viewer-status" class="viewer-status" aria-live="polite"><span id="viewer-status-dot" class="status-dot"></span><span id="viewer-status-text">Waiting for a Validated Result</span></div></div>
          <aside id="product-inspector" class="product-inspector" aria-label="Accepted Product" hidden><div class="product-inspector-heading"><div class="product-heading-copy"><span class="eyebrow">ACCEPTED PRODUCT</span><strong id="product-title">Product</strong><span id="product-summary"></span></div><div class="product-heading-actions"><span id="product-status" class="product-status">Accepted</span><details id="product-downloads" class="product-downloads"><summary>Files</summary><div id="product-download-list" class="product-download-list"></div></details></div></div><div class="product-tabs" role="tablist" aria-label="Product details"><button class="product-tab" type="button" role="tab" data-product-tab="structure">Structure</button><button class="product-tab" type="button" role="tab" data-product-tab="bom">BOM</button><button class="product-tab" type="button" role="tab" data-product-tab="validation">Validation</button><button class="product-tab" type="button" role="tab" data-product-tab="motion">Motion</button></div><div id="product-pane" class="product-pane" role="tabpanel" tabindex="0"></div></aside>
        </div>
      </section>
    </section><footer class="footer"><span>Trusted localhost demo · generated code runs in a bounded local process</span><span id="service-message"></span></footer>
  </main>`;

  const q = <T extends HTMLElement>(selector: string): T => {
    const element = document.querySelector<T>(selector);
    if (!element) throw new Error(`viewer element is missing: ${selector}`);
    return element;
  };
  return {
    app, projectList: q('#project-list'), traceLink: q('#trace-link'), projectCount: q('#project-count'), catalogEmpty: q('#catalog-empty'), projectEmpty: q('#project-empty'), projectContent: q('#project-content'), projectName: q('#project-name'), projectId: q('#project-id'), stateBadge: q('#state-badge'), harnessMetadata: q('#harness-metadata'), totalTokens: q('#total-tokens'), inputTokens: q('#input-tokens'), cachedTokens: q('#cached-tokens'), uncachedTokens: q('#uncached-tokens'), outputTokens: q('#output-tokens'), runTime: q('#run-time'), conversationCount: q('#conversation-count'), conversationList: q('#conversation-list'), promptInput: q('#prompt-input'), promptCounter: q('#prompt-counter'), runButton: q('#run-button'), stopButton: q('#stop-button'), clearButton: q('#clear-button'), deleteButton: q('#delete-button'), actionMessage: q('#action-message'), progressCount: q('#progress-count'), progressList: q('#progress-list'), viewerElement: q('#viewer'), viewerLoading: q('#viewer-loading'), viewerEmpty: q('#viewer-empty'), viewerEmptyTitle: q('#viewer-empty-title'), viewerEmptyCopy: q('#viewer-empty-copy'), viewerStatusText: q('#viewer-status-text'), viewerStatusDot: q('#viewer-status-dot'), viewerRotate: q('#viewer-rotate'), viewerReset: q('#viewer-reset'), viewerTitle: q('#viewer-title'), previewToggleControl: q('#preview-toggle-control'), previewToggle: q('#preview-toggle'), previewRetry: q('#preview-retry'), previewDetails: q('#preview-details'), previewLog: q('#preview-log'), productInspector: q('#product-inspector'), productTitle: q('#product-title'), productSummary: q('#product-summary'), productStatus: q('#product-status'), productDownloads: q('#product-downloads'), productDownloadList: q('#product-download-list'), productPane: q('#product-pane'), productTabButtons: Array.from(document.querySelectorAll<HTMLButtonElement>('[data-product-tab]')), serviceMessage: q('#service-message'), createProjectForm: q('#create-project-form'), refreshProjects: q('#refresh-projects'), fitButton: q('#fit-button'),
  };
}
