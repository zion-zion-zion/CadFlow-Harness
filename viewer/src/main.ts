import './style.css';

import { createShell } from './shell';
import { WorkspaceController } from './workspace-controller';
import type { ProductTab } from './product-inspector';
import type { ViewerDisplayMode } from './components/scene-viewer';

const shell = createShell();
const workspace = new WorkspaceController(shell);

for (const [index, button] of shell.productTabButtons.entries()) {
  button.addEventListener('click', () => workspace.productInspector.setTab(button.dataset.productTab as ProductTab));
  button.addEventListener('keydown', (event) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % shell.productTabButtons.length;
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + shell.productTabButtons.length) % shell.productTabButtons.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = shell.productTabButtons.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = shell.productTabButtons[nextIndex];
    workspace.productInspector.setTab(next.dataset.productTab as ProductTab);
    next.focus();
  });
}

shell.createProjectForm.addEventListener('submit', (event) => void workspace.createProject(event));
shell.refreshProjects.addEventListener('click', () => void workspace.refreshCatalog());
shell.runButton.addEventListener('click', () => void workspace.submitMessage());
shell.stopButton.addEventListener('click', () => void workspace.stopRun());
shell.clearButton.addEventListener('click', () => void workspace.clearConversation());
shell.deleteButton.addEventListener('click', () => void workspace.deleteProject());
shell.previewToggle.addEventListener('change', () => void workspace.setLivePreviewPaused());
shell.previewRetry.addEventListener('click', () => void workspace.retryLivePreview());
shell.fitButton.addEventListener('click', () => workspace.sceneViewer.fit());
shell.viewerRotate.addEventListener('click', () => {
  const enabled = !workspace.sceneViewer.isAutoRotate();
  workspace.sceneViewer.setAutoRotate(enabled);
  shell.viewerRotate.classList.toggle('is-active', enabled);
  shell.viewerRotate.setAttribute('aria-pressed', String(enabled));
});
shell.viewerReset.addEventListener('click', () => workspace.sceneViewer.resetView());
shell.workspaceSidebarToggle.addEventListener('click', () => {
  const expanded = shell.workspace.classList.toggle('is-sidebar-collapsed') === false;
  shell.workspaceSidebarToggle.setAttribute('aria-expanded', String(expanded));
  shell.workspaceSidebarToggle.setAttribute('aria-label', expanded ? 'Collapse workspace sidebar' : 'Expand workspace sidebar');
  shell.workspaceSidebarToggle.title = expanded ? 'Collapse workspace sidebar' : 'Expand workspace sidebar';
  shell.workspaceSidebarToggle.textContent = expanded ? '‹' : '›';
});
const setControlRailExpanded = (expanded: boolean): void => {
  shell.viewerControlRail.classList.toggle('is-collapsed', !expanded);
  shell.viewerControlRailToggle.setAttribute('aria-expanded', String(expanded));
  shell.viewerControlRailToggle.setAttribute('aria-label', expanded ? 'Collapse scene controls' : 'Expand scene controls');
  shell.viewerControlRailToggle.title = expanded ? 'Collapse scene controls' : 'Expand scene controls';
  shell.viewerControlRailToggle.textContent = expanded ? '−' : '+';
};
if (window.matchMedia('(max-width: 900px)').matches) setControlRailExpanded(false);
shell.viewerControlRailToggle.addEventListener('click', () => {
  setControlRailExpanded(shell.viewerControlRail.classList.contains('is-collapsed'));
});
shell.productInspectorToggle.addEventListener('click', () => workspace.productInspector.toggleExpanded());
shell.productInspectorClose.addEventListener('click', () => workspace.productInspector.close());
const displayModeButtons = [shell.viewerModeCinematic, shell.viewerModeTechnical];
const setDisplayMode = (mode: ViewerDisplayMode): void => {
  workspace.sceneViewer.setDisplayMode(mode);
  for (const button of displayModeButtons) {
    const selected = button.id === `viewer-mode-${mode}`;
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-selected', String(selected));
  }
};
for (const [index, button] of displayModeButtons.entries()) {
  button.addEventListener('click', () => setDisplayMode(index === 0 ? 'cinematic' : 'technical'));
  button.addEventListener('keydown', (event) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % displayModeButtons.length;
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + displayModeButtons.length) % displayModeButtons.length;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = displayModeButtons[nextIndex];
    setDisplayMode(nextIndex === 0 ? 'cinematic' : 'technical');
    next.focus();
  });
}
shell.promptInput.addEventListener('input', () => {
  shell.promptCounter.textContent = `${shell.promptInput.value.length.toLocaleString()} / 32000`;
  const project = workspace.currentProject();
  shell.runButton.disabled = !project || project.state === 'Running' || workspace.catalog.globalRunActive() || !shell.promptInput.value.trim();
});
shell.promptInput.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault();
    void workspace.submitMessage();
  }
});

window.addEventListener('beforeunload', () => workspace.dispose());
workspace.start();
