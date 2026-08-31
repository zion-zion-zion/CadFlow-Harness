import type { ProgressRecord } from './api';

export type SessionProgressHandler = (record: ProgressRecord) => void;
export type SessionPreviewHandler = () => void;
export type SessionErrorHandler = () => void;

/** Owns every resource whose lifetime is tied to the selected Project. */
export class ProjectSession {
  private selectedId: string | null = null;
  private generation = 0;
  private eventSource: EventSource | null = null;
  private sceneController: AbortController | null = null;
  private previewController: AbortController | null = null;
  private productController: AbortController | null = null;
  private progressHandler: SessionProgressHandler | null = null;
  private previewHandler: SessionPreviewHandler | null = null;
  private errorHandler: SessionErrorHandler | null = null;

  previewProjectId: string | null = null;
  latestPreviewRevision = 0;
  loadedPreviewRevision = 0;
  loadedSceneProjectId: string | null = null;
  loadedSceneArtifactVersion: number | null = null;
  loadedProductProjectId: string | null = null;
  loadedProductArtifactVersion: number | null = null;

  get projectId(): string | null { return this.selectedId; }
  get version(): number { return this.generation; }

  select(projectId: string): number {
    this.disposeResources();
    this.selectedId = projectId;
    this.generation += 1;
    this.resetLoadedState();
    return this.generation;
  }

  isCurrent(projectId: string, version = this.generation): boolean {
    return this.selectedId === projectId && this.generation === version;
  }

  openProgressStream(projectId: string, version: number, onProgress: SessionProgressHandler, onPreview: SessionPreviewHandler, onError?: SessionErrorHandler): EventSource | null {
    if (!this.isCurrent(projectId, version)) return null;
    this.closeProgressStream();
    const source = new EventSource(`/api/projects/${encodeURIComponent(projectId)}/events`);
    this.eventSource = source;
    this.progressHandler = onProgress;
    this.previewHandler = onPreview;
    this.errorHandler = onError ?? null;
    source.addEventListener('progress', (event) => {
      if (!this.isCurrent(projectId, version)) return;
      try { this.progressHandler?.(JSON.parse((event as MessageEvent).data) as ProgressRecord); }
      catch { this.errorHandler?.(); }
    });
    source.addEventListener('scene-preview', () => { if (this.isCurrent(projectId, version)) this.previewHandler?.(); });
    source.addEventListener('preview-status', () => { if (this.isCurrent(projectId, version)) this.previewHandler?.(); });
    return source;
  }

  closeProgressStream(): void {
    this.eventSource?.close();
    this.eventSource = null;
    this.progressHandler = null;
    this.previewHandler = null;
    this.errorHandler = null;
  }

  beginSceneRequest(): AbortController {
    this.sceneController?.abort();
    this.sceneController = new AbortController();
    return this.sceneController;
  }

  beginPreviewRequest(): AbortController {
    this.previewController?.abort();
    this.previewController = new AbortController();
    return this.previewController;
  }

  beginProductRequest(): AbortController {
    this.productController?.abort();
    this.productController = new AbortController();
    return this.productController;
  }

  finishSceneRequest(controller: AbortController): void { if (this.sceneController === controller) this.sceneController = null; }
  finishPreviewRequest(controller: AbortController): void { if (this.previewController === controller) this.previewController = null; }
  finishProductRequest(controller: AbortController): void { if (this.productController === controller) this.productController = null; }
  cancelSceneRequest(): void { this.sceneController?.abort(); this.sceneController = null; }
  cancelPreviewRequest(): void { this.previewController?.abort(); this.previewController = null; }
  cancelProductRequest(): void { this.productController?.abort(); this.productController = null; }

  /** Invalidate in-flight work while retaining the selected Project. */
  restart(): number {
    this.disposeResources();
    this.generation += 1;
    this.resetLoadedState();
    return this.generation;
  }

  resetLoadedState(): void {
    this.previewProjectId = null;
    this.latestPreviewRevision = 0;
    this.loadedPreviewRevision = 0;
    this.loadedSceneProjectId = null;
    this.loadedSceneArtifactVersion = null;
    this.loadedProductProjectId = null;
    this.loadedProductArtifactVersion = null;
  }

  disposeResources(): void {
    this.closeProgressStream();
    this.sceneController?.abort();
    this.previewController?.abort();
    this.productController?.abort();
    this.sceneController = null;
    this.previewController = null;
    this.productController = null;
  }

  dispose(): void {
    this.restart();
    this.selectedId = null;
  }
}
