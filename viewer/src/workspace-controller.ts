import { SceneViewer } from './components/scene-viewer';
import { request, type ConversationResponse, type ConversationTurn, type LivePreviewStatus, type MessageResponse, type Project, type ProgressRecord } from './api';
import { ProjectCatalog } from './catalog';
import { renderConversation } from './conversation';
import { errorMessage, formatDuration, formatTokenCount } from './formatters';
import { ProductInspector } from './product-inspector';
import { ProjectSession } from './project-session';
import { renderProgress } from './progress';
import type { ShellElements } from './shell';
import { ViewerCoordinator } from './viewer-coordinator';

const MAX_PROMPT_CHARS = 32_000;

export class WorkspaceController {
  readonly catalog = new ProjectCatalog();
  readonly session = new ProjectSession();
  readonly sceneViewer: SceneViewer;
  readonly productInspector: ProductInspector;
  readonly viewerCoordinator: ViewerCoordinator;
  private readonly conversationByProject = new Map<string, ConversationTurn[]>();
  private readonly progressByProject = new Map<string, ProgressRecord[]>();
  private workspaceMessage = '';
  private disposed = false;

  constructor(private readonly shell: ShellElements) {
    const currentProject = (): Project | null => this.currentProject();
    this.sceneViewer = new SceneViewer(shell.viewerElement, (message, ready) => {
      shell.viewerStatusText.textContent = message;
      shell.viewerStatusDot.classList.toggle('ready', ready);
      shell.viewerHudTitle.textContent = message;
      shell.viewerHudCopy.textContent = ready ? 'Canonical scene is ready for inspection.' : 'Preparing the scene workspace.';
    }, (nodeId) => this.productInspector?.selectNodeBySceneId(nodeId));
    this.productInspector = new ProductInspector(shell, this.sceneViewer, this.session, currentProject, () => this.session.projectId);
    this.sceneViewer.setMotionChangeHandler(() => this.productInspector.updateMotionControls());
    this.viewerCoordinator = new ViewerCoordinator(shell, this.sceneViewer, this.session, this.productInspector, currentProject, (message) => this.setMessage(message), () => this.renderAll());
  }

  start(): void { this.renderAll(); void this.refreshCatalog(); }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.session.dispose();
    this.sceneViewer.dispose();
  }

  currentProject(): Project | null { return this.catalog.get(this.session.projectId); }

  renderRunMetrics(project: Project | null): void {
    const terminal = project !== null && ['Succeeded', 'Failed', 'Stopped'].includes(project.state);
    const usage = terminal ? project.token_usage : null;
    this.setMetric(this.shell.totalTokens, formatTokenCount(usage?.total_tokens ?? null));
    this.setMetric(this.shell.inputTokens, formatTokenCount(usage?.input_tokens ?? null));
    this.setMetric(this.shell.cachedTokens, formatTokenCount(usage?.cached_input_tokens ?? null));
    this.setMetric(this.shell.uncachedTokens, formatTokenCount(usage?.uncached_input_tokens ?? null));
    this.setMetric(this.shell.outputTokens, formatTokenCount(usage?.output_tokens ?? null));
    this.setMetric(this.shell.runTime, formatDuration(terminal ? project.duration_seconds : null));
  }

  private setMetric(element: HTMLElement, value: string): void { element.textContent = value; element.title = value === '--' ? '' : value; }

  renderWorkspace(): void {
    const project = this.currentProject();
    this.shell.traceLink.href = project ? `/trace/${encodeURIComponent(project.project_id)}` : '/trace';
    const hasProject = project !== null;
    this.shell.projectEmpty.hidden = hasProject; this.shell.projectContent.hidden = !hasProject;
    if (!project) {
      this.shell.promptInput.value = ''; this.shell.promptInput.disabled = true; this.shell.runButton.disabled = true; this.shell.stopButton.hidden = true; this.shell.clearButton.disabled = true; this.shell.deleteButton.disabled = true; this.shell.actionMessage.textContent = ''; this.shell.harnessMetadata.textContent = ''; this.renderRunMetrics(null); this.shell.conversationCount.textContent = '0'; this.shell.conversationList.innerHTML = '<p class="panel-empty">No Project selected.</p>'; renderProgress(this.shell.progressList, this.shell.progressCount, [], null); return;
    }
    this.shell.projectName.textContent = project.name; this.shell.projectId.textContent = project.project_id; this.shell.harnessMetadata.textContent = 'Deep Agents'; this.shell.stateBadge.textContent = project.state; this.shell.stateBadge.className = `state-badge state-${project.state.toLowerCase()}`; this.renderRunMetrics(project); this.shell.promptCounter.textContent = `${this.shell.promptInput.value.length.toLocaleString()} / ${MAX_PROMPT_CHARS.toLocaleString()}`;
    const turns = this.conversationByProject.get(project.project_id) ?? [];
    renderConversation(this.shell.conversationList, this.shell.conversationCount, turns, this.catalog.globalRunActive(), (turn) => void this.retryTurn(turn));
    const anotherRunIsActive = this.catalog.globalRunActive() && project.state !== 'Running'; const composerDisabled = project.state === 'Running' || anotherRunIsActive;
    this.shell.promptInput.disabled = composerDisabled; this.shell.runButton.hidden = false; this.shell.runButton.textContent = turns.length === 0 ? 'Start Conversation' : 'Send'; this.shell.runButton.disabled = composerDisabled || !this.shell.promptInput.value.trim(); this.shell.stopButton.hidden = project.state !== 'Running'; this.shell.stopButton.disabled = project.state !== 'Running'; this.shell.clearButton.disabled = project.state === 'Running' || turns.length === 0; this.shell.deleteButton.disabled = false;
    if (this.workspaceMessage) this.shell.actionMessage.textContent = this.workspaceMessage; else if (anotherRunIsActive) this.shell.actionMessage.textContent = 'Another Project has an active Agent turn.'; else if (project.state === 'Failed' || project.state === 'Stopped') this.shell.actionMessage.textContent = project.failure_reason ?? 'The last turn did not complete.'; else this.shell.actionMessage.textContent = project.state === 'Succeeded' ? 'Ready for the next refinement.' : '';
    renderProgress(this.shell.progressList, this.shell.progressCount, this.progressByProject.get(project.project_id) ?? [], project);
  }

  renderAll(): void { this.catalog.render(this.shell.projectList, this.shell.catalogEmpty, this.shell.projectCount, this.session.projectId, (id) => void this.selectProject(id)); this.renderWorkspace(); this.viewerCoordinator.renderEmpty(); this.viewerCoordinator.renderChrome(); this.productInspector.render(); }
  private setMessage(message: string): void { this.workspaceMessage = message; this.renderWorkspace(); }
  private resetCurrentPresentation(resetTab = false): void { this.productInspector.reset(resetTab); this.session.resetLoadedState(); this.sceneViewer.clear(); }

  private async refreshSelectedProject(version: number): Promise<void> {
    const projectId = this.session.projectId; if (!projectId || !this.session.isCurrent(projectId, version)) return;
    try {
      const [project, conversation] = await Promise.all([request<Project>(`/api/projects/${encodeURIComponent(projectId)}`), request<ConversationResponse>(`/api/projects/${encodeURIComponent(projectId)}/messages`)]);
      if (!this.session.isCurrent(projectId, version)) return;
      this.catalog.upsert(project); this.conversationByProject.set(projectId, conversation.turns); if (!project.product_available || project.state === 'Running') this.productInspector.reset(); this.workspaceMessage = ''; this.renderAll();
      const loads: Promise<void>[] = []; if (this.viewerCoordinator.shouldLoadScene(project)) loads.push(this.viewerCoordinator.loadScene(project, version)); if (this.productInspector.shouldLoad(project)) loads.push(this.productInspector.load(project, version)); if (loads.length > 0) await Promise.all(loads);
      if ((!project.scene_available || project.state === 'Running') && this.session.loadedSceneProjectId === projectId) { this.session.loadedSceneProjectId = null; this.session.loadedSceneArtifactVersion = null; if (project.state === 'Failed' || project.state === 'Stopped') this.sceneViewer.markPreviewUnvalidated(); else this.sceneViewer.clear(); this.viewerCoordinator.renderEmpty(); this.productInspector.render(); }
      if ((project.state === 'Failed' || project.state === 'Stopped') && !project.scene_available) { this.sceneViewer.markPreviewUnvalidated(); await this.viewerCoordinator.loadLivePreview(project, version); } else if (project.state === 'Running') await this.viewerCoordinator.loadLivePreview(project, version);
    } catch (error) { if (this.session.isCurrent(projectId, version)) this.setMessage(errorMessage(error)); }
  }

  private openProgressStream(projectId: string, version: number): void {
    this.session.openProgressStream(projectId, version, (record) => { const events = this.progressByProject.get(projectId) ?? []; if (!events.some((item) => item.id === record.id)) events.push(record); this.progressByProject.set(projectId, events); this.renderWorkspace(); if (['completed', 'failed', 'stopped'].includes(record.stage) && this.currentProject()?.state === 'Running') void this.refreshSelectedProject(version); }, () => void this.viewerCoordinator.refreshLivePreviewStatus(projectId, version), () => this.setMessage('Progress Event could not be displayed.'));
  }

  async selectProject(projectId: string): Promise<void> {
    if (!this.catalog.has(projectId)) return;
    const version = this.session.select(projectId); this.workspaceMessage = ''; this.progressByProject.set(projectId, []); this.shell.promptInput.value = ''; this.resetCurrentPresentation(true); this.renderAll();
    try {
      const [project, conversation] = await Promise.all([request<Project>(`/api/projects/${encodeURIComponent(projectId)}`), request<ConversationResponse>(`/api/projects/${encodeURIComponent(projectId)}/messages`)]);
      if (!this.session.isCurrent(projectId, version)) return;
      this.catalog.upsert(project); this.conversationByProject.set(projectId, conversation.turns); this.renderAll(); this.openProgressStream(projectId, version);
      if (project.scene_available && project.state !== 'Running') { const loads: Promise<void>[] = [this.viewerCoordinator.loadScene(project, version)]; if (this.productInspector.shouldLoad(project)) loads.push(this.productInspector.load(project, version)); await Promise.all(loads); } else await this.viewerCoordinator.loadLivePreview(project, version);
    } catch (error) { if (this.session.isCurrent(projectId, version)) this.setMessage(errorMessage(error)); }
  }

  async refreshCatalog(): Promise<void> {
    try { const projects = await request<Project[]>('/api/projects'); this.catalog.setAll(projects); if (this.session.projectId && !this.catalog.has(this.session.projectId)) { this.session.dispose(); this.resetCurrentPresentation(true); } this.renderAll(); if (!this.session.projectId && projects[0]) await this.selectProject(projects[0].project_id); }
    catch (error) { this.shell.serviceMessage.textContent = errorMessage(error); }
  }

  async createProject(event: SubmitEvent): Promise<void> {
    event.preventDefault(); const input = this.shell.createProjectForm.querySelector<HTMLInputElement>('#new-project-name'); if (!input) return; const name = input.value.trim(); if (!name) { this.shell.serviceMessage.textContent = 'Project name must not be empty.'; input.focus(); return; }
    try { const project = await request<Project>('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); input.value = ''; this.catalog.upsert(project); await this.refreshCatalog(); if (this.session.projectId !== project.project_id) await this.selectProject(project.project_id); }
    catch (error) { this.shell.serviceMessage.textContent = errorMessage(error); }
  }

  private createRequestId(): string { return typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`; }

  async submitMessage(messageOverride?: string, retryOf?: string): Promise<void> {
    const project = this.currentProject(); const message = messageOverride ?? this.shell.promptInput.value; if (!project || project.state === 'Running' || this.catalog.globalRunActive()) return; if (!message.trim()) { this.setMessage('Message must not be empty.'); this.shell.promptInput.focus(); return; } if (message.length > MAX_PROMPT_CHARS) { this.setMessage(`Message exceeds the ${MAX_PROMPT_CHARS.toLocaleString()}-character limit.`); this.shell.promptInput.focus(); return; }
    const version = this.session.version; const requestId = this.createRequestId(); this.shell.runButton.disabled = true; this.workspaceMessage = ''; project.state = 'Running';
    const optimistic: ConversationTurn = { turn_id: requestId, sequence: (this.conversationByProject.get(project.project_id)?.length ?? 0) + 1, request_id: requestId, retry_of: retryOf ?? null, user_message: message.trim(), assistant_message: '', status: 'running', created_at: new Date().toISOString(), completed_at: null, artifact_version: null, error: null };
    this.conversationByProject.set(project.project_id, [...(this.conversationByProject.get(project.project_id) ?? []), optimistic]); if (messageOverride === undefined) this.shell.promptInput.value = ''; this.renderAll();
    try { const result = await request<MessageResponse>(`/api/projects/${encodeURIComponent(project.project_id)}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: message.trim(), request_id: requestId, retry_of: retryOf }) }); if (!this.session.isCurrent(project.project_id, version)) return; this.catalog.upsert(result.project); await this.refreshConversation(project.project_id, version); this.workspaceMessage = result.turn.status === 'succeeded' ? 'CAD turn completed. You can continue refining the model.' : result.turn.error ?? 'The CAD turn did not complete.'; this.renderAll(); await this.refreshCatalog(); await this.refreshSelectedProject(version); }
    catch (error) { if (this.session.isCurrent(project.project_id, version)) { this.setMessage(errorMessage(error)); await this.refreshSelectedProject(version); } }
  }

  private async retryTurn(turn: ConversationTurn): Promise<void> { await this.submitMessage(turn.user_message, turn.turn_id); }
  private async refreshConversation(projectId: string, version: number): Promise<void> { const conversation = await request<ConversationResponse>(`/api/projects/${encodeURIComponent(projectId)}/messages`); if (this.session.isCurrent(projectId, version)) this.conversationByProject.set(projectId, conversation.turns); }

  async stopRun(): Promise<void> { const project = this.currentProject(); if (!project || project.state !== 'Running') return; this.shell.stopButton.disabled = true; try { this.catalog.upsert(await request<Project>(`/api/projects/${encodeURIComponent(project.project_id)}/stop`, { method: 'POST' })); this.workspaceMessage = ''; this.renderAll(); await this.refreshCatalog(); } catch (error) { this.setMessage(errorMessage(error)); } }
  async setLivePreviewPaused(): Promise<void> { const project = this.currentProject(); if (!project || project.state !== 'Running') return; this.shell.previewToggle.disabled = true; try { const status = await request<LivePreviewStatus>(`/api/projects/${encodeURIComponent(project.project_id)}/preview/pause`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paused: !this.shell.previewToggle.checked }) }); project.preview = status; this.viewerCoordinator.renderChrome(); } catch (error) { this.shell.previewToggle.checked = project.preview.state !== 'paused'; this.setMessage(errorMessage(error)); } finally { this.shell.previewToggle.disabled = false; } }
  async retryLivePreview(): Promise<void> { const project = this.currentProject(); if (!project || project.state !== 'Running') return; this.shell.previewRetry.disabled = true; try { await request<{ accepted: boolean }>(`/api/projects/${encodeURIComponent(project.project_id)}/preview/retry`, { method: 'POST' }); } catch (error) { this.setMessage(errorMessage(error)); } finally { this.shell.previewRetry.disabled = false; } }
  async deleteProject(): Promise<void> { const project = this.currentProject(); if (!project || !window.confirm(`Permanently delete “${project.name}”?`)) return; try { await request<void>(`/api/projects/${encodeURIComponent(project.project_id)}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm_name: project.name }) }); this.catalog.remove(project.project_id); this.conversationByProject.delete(project.project_id); this.progressByProject.delete(project.project_id); this.session.dispose(); this.resetCurrentPresentation(true); await this.refreshCatalog(); } catch (error) { this.setMessage(errorMessage(error)); } }
  async clearConversation(): Promise<void> { const project = this.currentProject(); if (!project || project.state === 'Running' || !window.confirm(`Permanently clear the conversation and every CAD artifact in “${project.name}”?`)) return; this.shell.clearButton.disabled = true; const version = this.session.restart(); try { const reset = await request<Project>(`/api/projects/${encodeURIComponent(project.project_id)}/conversation`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm_name: project.name }) }); if (!this.session.isCurrent(project.project_id, version)) return; this.conversationByProject.set(project.project_id, []); this.progressByProject.set(project.project_id, []); this.catalog.upsert(reset); this.shell.promptInput.value = ''; this.resetCurrentPresentation(true); this.workspaceMessage = 'Conversation and CAD artifacts were cleared.'; this.renderAll(); } catch (error) { if (this.session.isCurrent(project.project_id, version)) this.setMessage(errorMessage(error)); } }
}
