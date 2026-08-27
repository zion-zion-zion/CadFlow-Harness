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
