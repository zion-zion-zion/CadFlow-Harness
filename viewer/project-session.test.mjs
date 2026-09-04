import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));
const server = await createServer({ root: viewerRoot, server: { middlewareMode: true }, logLevel: 'silent' });
const { ProjectSession } = await server.ssrLoadModule('/src/project-session.ts');
const { isPreviewCurrent, shouldRequestLivePreview } = await server.ssrLoadModule('/src/viewer-coordinator.ts');

class FakeEventSource {
  static instances = [];
  closed = false;
  listeners = new Map();
  constructor(url) { this.url = url; FakeEventSource.instances.push(this); }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  close() { this.closed = true; }
}

test.after(async () => { await server.close(); });

test('switching projects closes the stream and aborts every request', () => {
  globalThis.EventSource = FakeEventSource;
  const session = new ProjectSession();
  const firstVersion = session.select('first');
  const scene = session.beginSceneRequest();
  const preview = session.beginPreviewRequest();
  const product = session.beginProductRequest();
  session.openProgressStream('first', firstVersion, () => {}, () => {});
  const secondVersion = session.select('second');
  assert.equal(FakeEventSource.instances[0].closed, true);
  assert.equal(scene.signal.aborted, true);
  assert.equal(preview.signal.aborted, true);
  assert.equal(product.signal.aborted, true);
  assert.equal(session.isCurrent('first', firstVersion), false);
  assert.equal(session.isCurrent('second', secondVersion), true);
});

test('a project has at most one progress stream and dispose is repeatable', () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances.length = 0;
  const session = new ProjectSession();
  const version = session.select('project');
  session.openProgressStream('project', version, () => {}, () => {});
  session.openProgressStream('project', version, () => {}, () => {});
  assert.equal(FakeEventSource.instances.length, 2);
  assert.equal(FakeEventSource.instances[0].closed, true);
  assert.equal(FakeEventSource.instances[1].closed, false);
  assert.doesNotThrow(() => { session.dispose(); session.dispose(); });
  assert.equal(FakeEventSource.instances[1].closed, true);
});

test('preview revisions are accepted only for the current project and newest revision', () => {
  const base = { projectId: 'p', selectedProjectId: 'p', version: 4, selectedVersion: 4, projectState: 'Running', latestPreviewRevision: 8 };
  assert.equal(isPreviewCurrent({ ...base, previewRevision: 7 }), false);
  assert.equal(isPreviewCurrent({ ...base, previewRevision: 8 }), true);
  assert.equal(isPreviewCurrent({ ...base, selectedProjectId: 'other', previewRevision: 8 }), false);
  assert.equal(isPreviewCurrent({ ...base, projectState: 'Succeeded', previewRevision: 8 }), false);
});

test('terminal accepted scenes do not keep downloading stale live previews', () => {
  const preview = { artifact_available: true, revision: 3 };

  assert.equal(shouldRequestLivePreview({ state: 'Succeeded', preview }), false);
  assert.equal(shouldRequestLivePreview({ state: 'Draft', preview }), false);
  assert.equal(shouldRequestLivePreview({ state: 'Running', preview }), true);
  assert.equal(shouldRequestLivePreview({ state: 'Failed', preview }), true);
  assert.equal(shouldRequestLivePreview({ state: 'Stopped', preview }), true);
  assert.equal(shouldRequestLivePreview({ state: 'Running', preview: { artifact_available: false, revision: 3 } }), false);
});

test('resetting and disposing a session clears every loaded artifact identity', () => {
  const session = new ProjectSession();
  session.select('project');
  session.previewProjectId = 'project';
  session.latestPreviewRevision = 3;
  session.loadedPreviewRevision = 3;
  session.loadedSceneProjectId = 'project';
  session.loadedSceneArtifactVersion = 2;
  session.loadedProductProjectId = 'project';
  session.loadedProductArtifactVersion = 2;
  session.resetLoadedState();
  assert.equal(session.previewProjectId, null);
  assert.equal(session.latestPreviewRevision, 0);
  assert.equal(session.loadedPreviewRevision, 0);
  assert.equal(session.loadedSceneProjectId, null);
  assert.equal(session.loadedSceneArtifactVersion, null);
  assert.equal(session.loadedProductProjectId, null);
  assert.equal(session.loadedProductArtifactVersion, null);
  session.dispose();
  assert.equal(session.projectId, null);
});

test('restart invalidates in-flight requests without changing the selected Project', () => {
  const session = new ProjectSession();
  const version = session.select('project');
  const controller = session.beginSceneRequest();
  const restarted = session.restart();
  assert.equal(controller.signal.aborted, true);
  assert.equal(session.projectId, 'project');
  assert.equal(session.isCurrent('project', version), false);
  assert.equal(session.isCurrent('project', restarted), true);
});
