import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));

async function loadSceneState() {
  const server = await createServer({
    root: viewerRoot,
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  try {
    return await server.ssrLoadModule('/src/scene-state.ts');
  } finally {
    await server.close();
  }
}

test('reloads the canonical scene when a later terminal turn has a new artifact or preview', async () => {
  const { shouldLoadCanonicalScene } = await loadSceneState();

  assert.equal(shouldLoadCanonicalScene({
    projectId: 'project-1',
    state: 'Succeeded',
    sceneAvailable: true,
    artifactVersion: 2,
    loadedSceneProjectId: 'project-1',
    loadedSceneArtifactVersion: 1,
    previewProjectId: 'project-1',
  }), true);

  assert.equal(shouldLoadCanonicalScene({
    projectId: 'project-1',
    state: 'Failed',
    sceneAvailable: true,
    artifactVersion: 1,
    loadedSceneProjectId: 'project-1',
    loadedSceneArtifactVersion: 1,
    previewProjectId: 'project-1',
  }), true);

  assert.equal(shouldLoadCanonicalScene({
    projectId: 'project-1',
    state: 'Succeeded',
    sceneAvailable: true,
    artifactVersion: 2,
    loadedSceneProjectId: 'project-1',
    loadedSceneArtifactVersion: 2,
    previewProjectId: null,
  }), false);
});
