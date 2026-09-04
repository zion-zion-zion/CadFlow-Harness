import { ScenePackageError, type SceneViewer } from './components/scene-viewer';
import { shouldLoadCanonicalScene } from './scene-state';
import { request, requestArrayBuffer, requestBinary, type LivePreviewState, type LivePreviewStatus, type Project } from './api';
import type { ProjectSession } from './project-session';
import type { ShellElements } from './shell';
import type { ProductInspector } from './product-inspector';

export function isPreviewCurrent(params: {
  projectId: string;
  selectedProjectId: string | null;
  version: number;
  selectedVersion: number;
  projectState: Project['state'];
  previewRevision: number;
  latestPreviewRevision: number;
}): boolean {
  return params.selectedProjectId === params.projectId
    && params.version === params.selectedVersion
    && params.projectState !== 'Draft'
    && params.projectState !== 'Succeeded'
    && params.previewRevision === params.latestPreviewRevision;
}

export function shouldRequestLivePreview(project: Pick<Project, 'state' | 'preview'>): boolean {
  return project.state !== 'Draft'
    && project.state !== 'Succeeded'
    && project.preview.artifact_available
    && project.preview.revision >= 1;
}

export class ViewerCoordinator {
  constructor(
    private readonly elements: Pick<ShellElements, 'viewerLoading' | 'viewerEmpty' | 'viewerEmptyTitle' | 'viewerEmptyCopy' | 'viewerStatusText' | 'viewerStatusDot' | 'viewerTitle' | 'viewerHudTitle' | 'viewerHudCopy' | 'previewToggleControl' | 'previewToggle' | 'previewRetry' | 'previewDetails' | 'previewLog'>,
    private readonly sceneViewer: SceneViewer,
    private readonly session: ProjectSession,
    private readonly productInspector: ProductInspector,
    private readonly currentProject: () => Project | null,
    private readonly setMessage: (message: string) => void,
    private readonly renderAll: () => void,
  ) {}

  renderEmpty(): void {
    const project = this.currentProject();
    const visible = this.session.loadedSceneProjectId !== project?.project_id && this.session.previewProjectId !== project?.project_id;
    this.elements.viewerEmpty.hidden = !visible;
    if (!project) { this.elements.viewerEmptyTitle.textContent = 'No Project selected'; this.elements.viewerEmptyCopy.textContent = 'Select a Project to inspect its own Scene Artifact.'; }
    else if (project.state === 'Draft') { this.elements.viewerEmptyTitle.textContent = 'Draft Project'; this.elements.viewerEmptyCopy.textContent = 'Submit a complete Prompt to generate a Validated Result.'; }
    else if (project.state === 'Running') {
      if (project.preview.state === 'waiting') { this.elements.viewerEmptyTitle.textContent = 'Waiting for model'; this.elements.viewerEmptyCopy.textContent = 'No preview source is ready yet.'; }
      else if (project.preview.state === 'paused') { this.elements.viewerEmptyTitle.textContent = 'Preview paused'; this.elements.viewerEmptyCopy.textContent = 'The current preview is paused.'; }
      else if (project.preview.state === 'failed') { this.elements.viewerEmptyTitle.textContent = 'Preview failed'; this.elements.viewerEmptyCopy.textContent = project.preview.error ?? 'No usable preview is available.'; }
      else { this.elements.viewerEmptyTitle.textContent = 'Building live preview'; this.elements.viewerEmptyCopy.textContent = 'The latest model.py result will appear automatically.'; }
    } else if (project.state === 'Failed') { this.elements.viewerEmptyTitle.textContent = project.scene_available ? 'Loading last validated result' : 'No Validated Result'; this.elements.viewerEmptyCopy.textContent = project.scene_available ? 'The latest turn failed; the previous validated CAD version is still available.' : 'This Project has not produced a validated result yet.'; }
    else if (project.state === 'Stopped') { this.elements.viewerEmptyTitle.textContent = project.scene_available ? 'Loading last validated result' : 'Run stopped'; this.elements.viewerEmptyCopy.textContent = project.scene_available ? 'The stopped turn did not replace the previous validated CAD version.' : 'This Project has no validated Scene Artifact.'; }
    else { this.elements.viewerEmptyTitle.textContent = 'Loading Validated Result'; this.elements.viewerEmptyCopy.textContent = 'Fetching this Project’s canonical Scene Artifact.'; }
    this.elements.viewerHudTitle.textContent = this.elements.viewerEmptyTitle.textContent;
    this.elements.viewerHudCopy.textContent = this.elements.viewerEmptyCopy.textContent;
  }

  renderChrome(): void {
    const project = this.currentProject(); const running = project?.state === 'Running';
    this.elements.viewerTitle.textContent = running ? 'Live Preview' : project && ['Failed', 'Stopped'].includes(project.state) && project.scene_available ? 'Last Validated Result' : 'Validated Result';
    this.elements.previewToggleControl.hidden = !running; this.elements.previewRetry.hidden = !running || project.preview.state !== 'failed'; this.elements.previewToggle.checked = project?.preview.state !== 'paused'; this.elements.previewToggle.disabled = !running;
    const diagnostics = project ? [project.preview.error, project.preview.stderr, project.preview.stdout].filter(Boolean).join('\n\n') : ''; this.elements.previewDetails.hidden = diagnostics.length === 0; this.elements.previewLog.textContent = diagnostics;
    if (!project) { this.elements.viewerStatusDot.className = 'status-dot'; this.elements.viewerStatusText.textContent = 'Waiting for a Project'; this.syncHud('Awaiting a project selection', 'Select a project to inspect its accepted CAD scene.'); return; }
    if (project.scene_available && project.state !== 'Running') { this.elements.viewerStatusDot.className = `status-dot${this.session.loadedSceneProjectId === project.project_id ? ' ready' : ''}`; this.elements.viewerStatusText.textContent = this.session.loadedSceneProjectId === project.project_id ? `Validated Artifact${project.artifact_version ? ` · v${String(project.artifact_version).padStart(4, '0')}` : ''}` : 'Loading Validated Result'; this.syncHud(this.elements.viewerTitle.textContent, this.elements.viewerStatusText.textContent); return; }
    this.elements.viewerStatusDot.className = 'status-dot';
    if (project.state === 'Failed' || project.state === 'Stopped') { this.elements.viewerStatusText.textContent = project.preview.artifact_available ? 'Last preview · unvalidated' : 'No live preview available'; this.elements.viewerStatusDot.classList.toggle('error', project.state === 'Failed'); this.syncHud(this.elements.viewerTitle.textContent, this.elements.viewerStatusText.textContent); return; }
    if (project.state === 'Draft') { this.elements.viewerStatusText.textContent = 'Live preview starts with the Agent Run'; this.syncHud(this.elements.viewerTitle.textContent, this.elements.viewerStatusText.textContent); return; }
    const labels: Record<LivePreviewState, string> = { waiting: 'Waiting for model.py', stale: 'Source changed · preview stale', building: 'Building live preview', current: 'Live preview current · unvalidated', failed: project.preview.artifact_available ? 'Preview failed · last result retained' : 'Preview failed', paused: 'Live preview paused' };
    this.elements.viewerStatusText.textContent = labels[project.preview.state]; this.elements.viewerStatusDot.classList.toggle('ready', project.preview.state === 'current'); this.elements.viewerStatusDot.classList.toggle('building', project.preview.state === 'building'); this.elements.viewerStatusDot.classList.toggle('error', project.preview.state === 'failed'); this.syncHud(this.elements.viewerTitle.textContent, this.elements.viewerStatusText.textContent);
  }

  private syncHud(title: string, copy: string): void {
    this.elements.viewerHudTitle.textContent = title;
    this.elements.viewerHudCopy.textContent = copy;
  }

  async loadScene(project: Project, version: number): Promise<void> {
    if (!project.scene_available || project.state === 'Running' || !this.session.isCurrent(project.project_id, version)) return;
    const controller = this.session.beginSceneRequest();
    this.session.loadedSceneProjectId = null; this.session.loadedSceneArtifactVersion = null;
    this.session.cancelPreviewRequest(); this.session.previewProjectId = null; this.session.latestPreviewRevision = 0; this.session.loadedPreviewRevision = 0;
    this.sceneViewer.clear('Loading canonical Scene Artifact'); this.productInspector.render(); this.elements.viewerEmpty.hidden = true; this.elements.viewerLoading.hidden = false;
    try {
      const blob = await requestBinary(`/api/projects/${encodeURIComponent(project.project_id)}/scene`, { signal: controller.signal }, (status) => `Scene Artifact request failed (${status})`);
      if (!this.session.isCurrent(project.project_id, version)) return;
      await this.sceneViewer.load(blob);
      if (this.productInspector.semanticModel) this.sceneViewer.setMotionModel(this.productInspector.semanticModel);
      if (this.session.isCurrent(project.project_id, version)) { this.session.loadedSceneProjectId = project.project_id; this.session.loadedSceneArtifactVersion = project.artifact_version; this.renderEmpty(); this.renderChrome(); this.productInspector.render(); }
    } catch (error) {
      if (controller.signal.aborted || !this.session.isCurrent(project.project_id, version)) return;
      this.sceneViewer.clear(); this.setMessage(error instanceof ScenePackageError || error instanceof Error ? error.message : 'The request could not be completed.'); this.renderEmpty();
    } finally { this.session.finishSceneRequest(controller); this.elements.viewerLoading.hidden = true; this.productInspector.render(); }
  }

  private isCurrentPreview(projectId: string, version: number, revision: number): boolean {
    const project = this.currentProject();
    return project !== null && isPreviewCurrent({ projectId, selectedProjectId: this.session.projectId, version, selectedVersion: this.session.version, projectState: project.state, previewRevision: project.preview.revision, latestPreviewRevision: this.session.latestPreviewRevision });
  }

  async loadLivePreview(project: Project, version: number): Promise<void> {
    if (!shouldRequestLivePreview(project) || !this.session.isCurrent(project.project_id, version)) return;
    const revision = project.preview.revision;
    if (this.session.previewProjectId === project.project_id && this.session.loadedPreviewRevision === revision) return;
    const controller = this.session.beginPreviewRequest(); this.session.latestPreviewRevision = revision;
    try {
      const payload = await requestArrayBuffer(`/api/projects/${encodeURIComponent(project.project_id)}/preview`, { signal: controller.signal, headers: { Accept: 'model/gltf-binary' } }, (status) => `Preview request failed (${status})`);
      if (!this.isCurrentPreview(project.project_id, version, revision)) return;
      const displayed = await this.sceneViewer.loadPreview(payload, project.state === 'Running' ? 'Live preview' : 'Last preview', () => this.isCurrentPreview(project.project_id, version, revision));
      if (displayed && this.isCurrentPreview(project.project_id, version, revision)) { this.session.previewProjectId = project.project_id; this.session.loadedPreviewRevision = revision; this.renderEmpty(); this.renderChrome(); }
    } catch (error) {
      if (controller.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return;
      if (this.session.isCurrent(project.project_id, version)) this.setMessage('Live preview could not be displayed.');
    } finally { this.session.finishPreviewRequest(controller); }
  }

  async refreshLivePreviewStatus(projectId: string, version: number): Promise<void> {
    if (!this.session.isCurrent(projectId, version)) return;
    try {
      const status = await request<LivePreviewStatus>(`/api/projects/${encodeURIComponent(projectId)}/preview/status`); const project = this.currentProject();
      if (!project || project.project_id !== projectId || !this.session.isCurrent(projectId, version)) return;
      project.preview = status; this.renderEmpty(); this.renderChrome(); await this.loadLivePreview(project, version);
    } catch (error) { if (this.session.isCurrent(projectId, version)) this.setMessage(error instanceof Error ? error.message : 'The request could not be completed.'); }
  }

  shouldLoadScene(project: Project): boolean {
    return shouldLoadCanonicalScene({ projectId: project.project_id, state: project.state, sceneAvailable: project.scene_available, artifactVersion: project.artifact_version, loadedSceneProjectId: this.session.loadedSceneProjectId, loadedSceneArtifactVersion: this.session.loadedSceneArtifactVersion, previewProjectId: this.session.previewProjectId });
  }
}
