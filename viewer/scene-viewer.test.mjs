import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));

async function loadSceneViewer() {
  const server = await createServer({
    root: viewerRoot,
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  try {
    return await server.ssrLoadModule('/src/components/scene-viewer.ts');
  } finally {
    await server.close();
  }
}

test('preview preparation preserves source GLB materials', async () => {
  const { preparePreviewScene } = await loadSceneViewer();
  const sourceMaterial = new THREE.MeshStandardMaterial({
    color: new THREE.Color(0.91, 0.17, 0.63),
    metalness: 0.74,
    roughness: 0.22,
    side: THREE.BackSide,
  });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), sourceMaterial);
  const scene = new THREE.Group();
  scene.add(mesh);

  const prepared = preparePreviewScene(scene);

  assert.equal(prepared, scene);
  assert.equal(mesh.material, sourceMaterial);
  assert.deepEqual(mesh.material.color.toArray(), [0.91, 0.17, 0.63]);
  assert.equal(mesh.material.metalness, 0.74);
  assert.equal(mesh.material.roughness, 0.22);
  assert.equal(mesh.material.side, THREE.BackSide);
});

test('final scene material uses the complete manifest appearance', async () => {
  const { materialFromAppearance } = await loadSceneViewer();
  const material = materialFromAppearance({
    appearance_id: 'appearance/custom',
    base_color: [0.91, 0.17, 0.63, 0.4],
    metallic: 0.74,
    roughness: 0.22,
    alpha_mode: 'blend',
    double_sided: true,
  });

  assert.deepEqual(material.color.toArray(), [0.91, 0.17, 0.63]);
  assert.equal(material.opacity, 0.4);
  assert.equal(material.transparent, true);
  assert.equal(material.metalness, 0.74);
  assert.equal(material.roughness, 0.22);
  assert.equal(material.side, THREE.DoubleSide);
});

test('generic CadFlow appearance gets the studio steel finish', async () => {
  const { materialFromAppearance } = await loadSceneViewer();
  const material = materialFromAppearance({
    appearance_id: 'appearance/evaluated/default',
    base_color: [0.72, 0.75, 0.78, 1],
    metallic: 0,
    roughness: 0.55,
    alpha_mode: 'opaque',
    double_sided: false,
  });

  assert.deepEqual(material.color.toArray(), [0.38, 0.45, 0.54]);
  assert.equal(material.metalness, 0.45);
  assert.equal(material.roughness, 0.34);
  assert.equal(material.transparent, false);
});

test('render quality drops one tier when the frame budget is missed', async () => {
  const { nextRenderQuality } = await loadSceneViewer();

  assert.deepEqual(nextRenderQuality('high', 24, 0), { quality: 'medium', goodWindows: 0 });
  assert.deepEqual(nextRenderQuality('medium', 24, 0), { quality: 'low', goodWindows: 0 });
  assert.deepEqual(nextRenderQuality('low', 24, 0), { quality: 'low', goodWindows: 0 });
});

test('render quality only upgrades after three stable windows', async () => {
  const { nextRenderQuality } = await loadSceneViewer();

  assert.deepEqual(nextRenderQuality('low', 10, 0), { quality: 'low', goodWindows: 1 });
  assert.deepEqual(nextRenderQuality('low', 10, 1), { quality: 'low', goodWindows: 2 });
  assert.deepEqual(nextRenderQuality('low', 10, 2), { quality: 'medium', goodWindows: 0 });
  assert.deepEqual(nextRenderQuality('high', 10, 2), { quality: 'high', goodWindows: 0 });
});

test('render quality resets its recovery counter when performance is marginal', async () => {
  const { nextRenderQuality } = await loadSceneViewer();

  assert.deepEqual(nextRenderQuality('medium', 17, 2), { quality: 'medium', goodWindows: 0 });
});
