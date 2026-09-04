import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));

function srgbChannels(color) {
  return color.clone().convertLinearToSRGB().toArray();
}

function assertChannels(actual, expected, tolerance = 1e-4) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => {
    assert.ok(Math.abs(value - expected[index]) < tolerance, `${value} != ${expected[index]}`);
  });
}

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

test('validated and preview scene layers are mutually exclusive', async () => {
  const { activateSceneLayer } = await loadSceneViewer();
  const modelRoot = new THREE.Group();
  const previewRoot = new THREE.Group();

  activateSceneLayer(modelRoot, previewRoot, 'model');
  assert.equal(modelRoot.visible, true);
  assert.equal(previewRoot.visible, false);

  activateSceneLayer(modelRoot, previewRoot, 'preview');
  assert.equal(modelRoot.visible, false);
  assert.equal(previewRoot.visible, true);

  activateSceneLayer(modelRoot, previewRoot, 'none');
  assert.equal(modelRoot.visible, false);
  assert.equal(previewRoot.visible, false);
});

test('preview root keeps the coordinate transform embedded by the Harness', async () => {
  const { preparePreviewRoot } = await loadSceneViewer();
  const root = new THREE.Group();
  root.position.set(1, 2, 3);
  root.rotation.x = Math.PI / 2;
  root.scale.setScalar(2);

  preparePreviewRoot(root);

  assert.equal(root.name, 'live-preview');
  assert.deepEqual(root.position.toArray(), [0, 0, 0]);
  assert.deepEqual(root.rotation.toArray().slice(0, 3), [0, 0, 0]);
  assert.deepEqual(root.scale.toArray(), [1, 1, 1]);
  assert.deepEqual(root.matrix.toArray(), new THREE.Matrix4().toArray());
});

test('final scene material uses the complete manifest appearance', async () => {
  const { materialFromAppearance } = await loadSceneViewer();
  const material = materialFromAppearance({
    appearance_id: 'appearance/custom',
    name: 'painted-steel',
    base_color: [0.91, 0.17, 0.63, 0.4],
    metallic: 0.74,
    roughness: 0.22,
    alpha_mode: 'blend',
    double_sided: true,
  });

  assertChannels(srgbChannels(material.color), [0.91, 0.17, 0.63]);
  assert.equal(material.opacity, 0.4);
  assert.equal(material.transparent, true);
  assert.equal(material.metalness, 0.24);
  assert.equal(material.roughness, 0.24);
  assert.equal(material.clearcoat, 0.52);
  assert.equal(material.side, THREE.DoubleSide);
});

test('authored appearances select visibly distinct physical surface models', async () => {
  const { appearanceSurface, materialFromAppearance } = await loadSceneViewer();
  const base = {
    appearance_id: 'appearance/test',
    base_color: [0.12, 0.48, 0.75, 1],
    metallic: 0.12,
    roughness: 0.1,
    alpha_mode: 'opaque',
    double_sided: false,
  };
  const glass = { ...base, name: 'blue-cab-glass' };
  const rubber = { ...base, name: 'matte-black-rubber', metallic: 0, roughness: 0.86 };
  const alloy = { ...base, name: 'brushed-silver-alloy', metallic: 0.95, roughness: 0.3 };

  assert.equal(appearanceSurface(glass), 'glass');
  assert.equal(appearanceSurface(rubber), 'rubber');
  assert.equal(appearanceSurface(alloy), 'bare-metal');

  const glassMaterial = materialFromAppearance(glass);
  assert.equal(glassMaterial.transmission, 0.14);
  assert.equal(glassMaterial.ior, 1.48);
  assert.equal(glassMaterial.clearcoat, 0.82);
  assert.equal(glassMaterial.opacity, 0.76);
  assert.equal(glassMaterial.depthWrite, false);

  const rubberMaterial = materialFromAppearance(rubber);
  assert.equal(rubberMaterial.metalness, 0);
  assert.equal(rubberMaterial.roughness, 0.86);
  assert.equal(rubberMaterial.sheen, 0.22);
  assert.equal(rubberMaterial.envMapIntensity, 0.32);

  const alloyMaterial = materialFromAppearance(alloy);
  assert.equal(alloyMaterial.metalness, 0.95);
  assert.equal(alloyMaterial.roughness, 0.3);
  assert.equal(alloyMaterial.envMapIntensity, 1.42);
});

test('eye lenses use transmissive glass with a restrained emissive response', async () => {
  const { materialFromAppearance } = await loadSceneViewer();
  const material = materialFromAppearance({
    appearance_id: 'appearance/eye',
    name: 'cyan-eye-lens',
    base_color: [0.02, 0.88, 1, 1],
    metallic: 0.05,
    roughness: 0.08,
    alpha_mode: 'opaque',
    double_sided: false,
  });

  assert.equal(material.transmission, 0.08);
  assert.equal(material.emissiveIntensity, 0.32);
  assertChannels(srgbChannels(material.emissive), [0.02, 0.88, 1]);
});

test('cinematic mode removes CAD edge overlays while technical mode retains them', async () => {
  const { edgeVisualStyle } = await loadSceneViewer();

  assert.deepEqual(edgeVisualStyle('cinematic'), { visible: false, linewidth: 1.6 });
  assert.deepEqual(edgeVisualStyle('technical'), { visible: true, linewidth: 2.15 });
});

test('cinematic vignette darkens instead of bleaching the scene background', async () => {
  const { cinematicVignetteUniforms } = await loadSceneViewer();
  const vignette = cinematicVignetteUniforms();

  assert.deepEqual(vignette, { offset: 0.82, darkness: 1 });
  assert.ok(1 - vignette.darkness <= 0, 'VignetteShader target color must not be lighter than black');
});

test('default camera frames the conventional negative-Y product front', async () => {
  const { defaultCameraDirection } = await loadSceneViewer();
  const direction = defaultCameraDirection();

  assert.ok(direction.x > 0);
  assert.ok(direction.y < 0);
  assert.ok(direction.z > 0);
  assert.ok(Math.abs(direction.length() - 1) < 1e-12);
});

test('node appearance overrides take precedence over definition materials', async () => {
  const { resolvedAppearanceId } = await loadSceneViewer();

  assert.equal(resolvedAppearanceId('appearance/instance', 'appearance/definition'), 'appearance/instance');
  assert.equal(resolvedAppearanceId(null, 'appearance/definition'), 'appearance/definition');
  assert.equal(resolvedAppearanceId(undefined, undefined), undefined);
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

  assertChannels(srgbChannels(material.color), [0.38, 0.45, 0.54]);
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
