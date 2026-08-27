import { strFromU8, unzipSync } from 'fflate';
import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex } from '@noble/hashes/utils';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

import { nodeIsInIsolation } from '../product-state';

type Vec3 = [number, number, number];
type Transform = { origin: Vec3; x_axis: Vec3; y_axis: Vec3; z_axis: Vec3 };
export type Appearance = {
  appearance_id: string;
  base_color: [number, number, number, number];
  metallic: number;
  roughness: number;
  alpha_mode: 'opaque' | 'mask' | 'blend';
  double_sided: boolean;
};
type Definition = {
  definition_id: string;
  kind: string;
  name: string | null;
  geometry_asset_id?: string;
  edge_asset_id?: string;
  appearance_id?: string;
};
type SceneNode = {
  node_id: string;
  parent_node_id: string | null;
  order: number;
  definition_id: string;
  transform: Transform;
  visible: boolean;
};
type Asset = {
  asset_id: string;
  uri: string;
  byte_length: number;
  content_hash: string;
  asset_to_scene: number[];
};
type EntityAsset = {
  entity_asset_id: string;
  uri: string;
  byte_length: number;
  content_hash: string;
};
type SourceFile = {
  path: string;
  uri: string;
  byte_length: number;
  content_hash: string;
};
type EmbeddedSource = {
  artifact_hash?: string;
  embedded_artifact_uri?: string;
  embedded_artifact_byte_length?: number;
  source_files?: SourceFile[];
};
type SceneManifest = {
  schema_version: string;
  scene_id: string;
  generator: { profile: string; name?: string; cadflow_version?: string };
  source: EmbeddedSource;
  presentation_source?: EmbeddedSource;
  coordinate_system: { length_unit: string };
  definitions: Definition[];
  nodes: SceneNode[];
  geometry_assets: Asset[];
  edge_assets: Asset[];
  entity_assets: EntityAsset[];
  appearances: Appearance[];
};
type PackageFiles = Record<string, Uint8Array>;
type PackageRecord = { uri: string; byte_length: number; content_hash: string };

const MAX_PACKAGE_BYTES = 256 * 1024 * 1024;
const MAX_PACKAGE_MEMBERS = 10_000;
const MAX_UNPACKED_BYTES = 512 * 1024 * 1024;
const MAX_MEMBER_BYTES = 256 * 1024 * 1024;
const MAX_SCENE_JSON_BYTES = 8 * 1024 * 1024;
const MAX_COMPRESSION_RATIO = 100;
const MAX_PREVIEW_BYTES = 16 * 1024 * 1024;
const CAD_EDGE_LIGHTNESS_OFFSET = 0.5;
const CAD_EDGE_LINE_WIDTH = 1.6;

export type SceneViewerStatus = (message: string, ready: boolean) => void;
export type SceneNodeSelection = (nodeId: string) => void;

export class ScenePackageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ScenePackageError';
  }
}

export function preparePreviewScene(scene: THREE.Object3D): THREE.Object3D {
  scene.name = 'preview-model';
  return scene;
}

export function materialFromAppearance(appearance?: Appearance): THREE.MeshStandardMaterial {
  const color = appearance?.base_color ?? [0.72, 0.75, 0.78, 1];
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(color[0], color[1], color[2]),
    metalness: appearance?.metallic ?? 0,
    roughness: appearance?.roughness ?? 0.55,
    side: appearance?.double_sided ? THREE.DoubleSide : THREE.FrontSide,
    transparent: appearance?.alpha_mode === 'blend',
    opacity: color[3],
  });
}

export class SceneViewer {
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
  private readonly renderer: THREE.WebGLRenderer;
  private readonly controls: OrbitControls;
  private readonly loader = new GLTFLoader();
  private readonly modelRoot = new THREE.Group();
  private readonly previewRoot = new THREE.Group();
  private readonly geometryCache = new Map<string, THREE.Object3D>();
  private readonly edgeCache = new Map<string, THREE.Object3D>();
  private readonly definitions = new Map<string, Definition>();
  private readonly appearances = new Map<string, Appearance>();
  private readonly sceneNodes = new Map<string, THREE.Object3D>();
  private readonly onStatus: SceneViewerStatus;
  private readonly onNodeSelected: SceneNodeSelection;
  private currentManifest: SceneManifest | null = null;
  private currentFiles: PackageFiles | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private previewHasFramed = false;
  private selectionHelper: THREE.BoxHelper | null = null;
  private selectedNodeId: string | null = null;
  private pointerStart: { x: number; y: number } | null = null;

  constructor(
    container: HTMLElement,
    onStatus: SceneViewerStatus = () => undefined,
    onNodeSelected: SceneNodeSelection = () => undefined,
  ) {
    this.onStatus = onStatus;
    this.onNodeSelected = onNodeSelected;
    this.scene.background = new THREE.Color('#0b0e12');
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(2.4, 2.1, 3.0);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    container.append(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 0.01;
    this.controls.maxDistance = 1000;

    this.scene.add(new THREE.HemisphereLight('#d9e9ff', '#10141b', 1.7));
    const keyLight = new THREE.DirectionalLight('#fff8e9', 3.1);
    keyLight.position.set(4, 7, 5);
    this.scene.add(keyLight, keyLight.target);
    const fillLight = new THREE.DirectionalLight('#8db4ff', 1.1);
    fillLight.position.set(-5, -6, 7);
    this.scene.add(fillLight, fillLight.target);

    this.modelRoot.name = 'validated-scene';
    this.previewRoot.name = 'live-preview';
    // Native preview GLBs use glTF's Y-up basis: (x, z, -y) / 1000.
    // The application scene is Z-up, so rotate the preview into that basis.
    this.previewRoot.rotation.x = Math.PI / 2;
    this.scene.add(this.modelRoot, this.previewRoot);
    this.renderer.domElement.addEventListener('pointerdown', this.handlePointerDown);
    this.renderer.domElement.addEventListener('pointerup', this.handlePointerUp);
    this.resizeObserver = new ResizeObserver(() => this.resize(container));
    this.resizeObserver.observe(container);
    this.resize(container);
    this.renderer.setAnimationLoop(() => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  async load(blob: Blob): Promise<void> {
    this.clearModel();
    this.onStatus('Checking Scene Artifact', false);
    try {
      if (blob.size > MAX_PACKAGE_BYTES) {
        throw new ScenePackageError('Scene Artifact exceeds the browser size limit');
      }
      const raw = new Uint8Array(await blob.arrayBuffer());
      const files = unzipPackage(raw);
      const manifest = JSON.parse(strFromU8(bytesFor(files, 'scene.json'))) as SceneManifest;
      if (manifest.schema_version !== '1.0') {
        throw new ScenePackageError(`Unsupported Scene schema: ${manifest.schema_version}`);
      }
      await validatePackageMembers(files, manifest);

      this.currentManifest = manifest;
      this.currentFiles = files;
      this.definitions.clear();
      this.appearances.clear();
      for (const definition of manifest.definitions) this.definitions.set(definition.definition_id, definition);
      for (const appearance of manifest.appearances) this.appearances.set(appearance.appearance_id, appearance);
      await this.buildScene();
      this.frame();
      this.onStatus(
        `${manifest.geometry_assets.length} geometry asset${manifest.geometry_assets.length === 1 ? '' : 's'} · ${manifest.coordinate_system.length_unit}`,
        true,
      );
    } catch (error) {
      this.clearModel();
      throw error;
    }
  }

  async loadPreview(
    payload: ArrayBuffer,
    label: string,
    isCurrent: () => boolean = () => true,
  ): Promise<boolean> {
    if (payload.byteLength > MAX_PREVIEW_BYTES) {
      throw new ScenePackageError('Preview frame exceeds the browser size limit');
    }
    let next: THREE.Object3D;
    try {
      const gltf = await this.loader.parseAsync(payload, '');
      next = gltf.scene;
    } catch (error) {
      throw new ScenePackageError(
        `Preview frame is not a valid CadFlow GLB: ${error instanceof Error ? error.message : 'parse failed'}`,
      );
    }
    preparePreviewScene(next);
    if (!isCurrent()) {
      this.disposePreviewObject(next);
      return false;
    }
    // Parsing completes before this swap, so a failed or stale frame leaves the
    // last usable preview visible and its GPU resources intact.
    this.clearPreview();
    this.previewRoot.add(next);
    if (!this.previewHasFramed) {
      this.frame(this.previewRoot);
      this.previewHasFramed = true;
    }
    this.onStatus(`${label} · unvalidated`, false);
    return true;
  }

  markPreviewUnvalidated(): void {
    if (this.previewRoot.children.length > 0) this.onStatus('Last preview · unvalidated', false);
  }

  clear(message = 'No validated Scene Artifact'): void {
    this.clearModel();
    this.onStatus(message, false);
  }

  fit(): void {
    this.frame(this.modelRoot.children.length > 0 ? this.modelRoot : this.previewRoot);
  }

  hasNode(nodeId: string): boolean {
    return this.sceneNodes.has(nodeId);
  }

  isNodeVisible(nodeId: string): boolean {
    return this.sceneNodes.get(nodeId)?.visible ?? false;
  }

  selectNode(nodeId: string | null): boolean {
    this.clearSelection();
    if (nodeId === null) return true;
    const object = this.sceneNodes.get(nodeId);
    if (!object) return false;
    object.updateWorldMatrix(true, true);
    const helper = new THREE.BoxHelper(object, new THREE.Color('#f1c86b'));
    helper.name = 'product-selection';
    helper.material.depthTest = false;
    helper.material.transparent = true;
    helper.material.opacity = 0.9;
    helper.renderOrder = 20;
    this.selectionHelper = helper;
    this.selectedNodeId = nodeId;
    this.scene.add(helper);
    return true;
  }

  setNodeVisible(nodeId: string, visible: boolean): boolean {
    const object = this.sceneNodes.get(nodeId);
    if (!object) return false;
    object.visible = visible;
    if (visible) {
      for (const [candidateId, candidate] of this.sceneNodes) {
        if (nodeId.startsWith(`${candidateId}/`)) candidate.visible = true;
      }
    }
    if (!visible && this.selectedNodeId !== null && nodeIsInIsolation(this.selectedNodeId, nodeId)) {
      this.clearSelection();
    }
    return true;
  }

  isolateNode(nodeId: string): boolean {
    if (!this.sceneNodes.has(nodeId)) return false;
    for (const [candidateId, object] of this.sceneNodes) {
      object.visible = nodeIsInIsolation(candidateId, nodeId);
    }
    this.selectNode(nodeId);
    return true;
  }

  showAll(): void {
    for (const object of this.sceneNodes.values()) object.visible = true;
  }

  dispose(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.renderer.setAnimationLoop(null);
    this.controls.dispose();
    this.renderer.domElement.removeEventListener('pointerdown', this.handlePointerDown);
    this.renderer.domElement.removeEventListener('pointerup', this.handlePointerUp);
    this.clearModel();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  private async buildScene(): Promise<void> {
    if (!this.currentManifest) throw new ScenePackageError('Scene manifest is not loaded');
    const nodesByParent = new Map<string | null, SceneNode[]>();
    for (const node of this.currentManifest.nodes) {
      const siblings = nodesByParent.get(node.parent_node_id) ?? [];
      siblings.push(node);
      nodesByParent.set(node.parent_node_id, siblings);
    }
    for (const siblings of nodesByParent.values()) siblings.sort((left, right) => left.order - right.order);

    const build = async (parent: THREE.Object3D, parentId: string | null): Promise<void> => {
      for (const node of nodesByParent.get(parentId) ?? []) {
        const definition = this.definitions.get(node.definition_id);
        if (!definition) throw new ScenePackageError(`Scene definition is missing: ${node.definition_id}`);
        const object = new THREE.Group();
        object.name = node.node_id;
        object.matrixAutoUpdate = false;
        object.matrix.copy(placementMatrix(node.transform));
        object.visible = node.visible;
        object.userData.sceneNodeId = node.node_id;
        this.sceneNodes.set(node.node_id, object);
        parent.add(object);
        if (definition.kind !== 'assembly') object.add(await this.instantiateDefinition(definition));
        await build(object, node.node_id);
      }
    };
    await build(this.modelRoot, null);
  }

  private async instantiateDefinition(definition: Definition): Promise<THREE.Group> {
    if (!this.currentManifest || !this.currentFiles) throw new ScenePackageError('Scene package is not loaded');
    const group = new THREE.Group();
    group.name = definition.name || definition.definition_id;
    const geometryAsset = this.currentManifest.geometry_assets.find((asset) => asset.asset_id === definition.geometry_asset_id);
    if (geometryAsset) {
      let geometry = this.geometryCache.get(geometryAsset.asset_id);
      if (!geometry) {
        geometry = await this.loadGlb(geometryAsset.uri);
        applyAssetTransform(geometry, geometryAsset.asset_to_scene);
        this.geometryCache.set(geometryAsset.asset_id, geometry);
      }
      const renderGeometry = geometry.clone(true);
      renderGeometry.traverse((child) => {
        if (child instanceof THREE.Mesh) child.material = this.materialFor(definition);
      });
      group.add(renderGeometry);
    }
    const edgeAsset = this.currentManifest.edge_assets.find((asset) => asset.asset_id === definition.edge_asset_id);
    if (edgeAsset) {
      let edge = this.edgeCache.get(edgeAsset.asset_id);
      if (!edge) {
        edge = await this.loadGlb(edgeAsset.uri);
        applyAssetTransform(edge, edgeAsset.asset_to_scene);
        this.edgeCache.set(edgeAsset.asset_id, edge);
      }
      const edgeInstance = edge.clone(true);
      edgeInstance.traverse((child) => {
        if (child instanceof THREE.LineSegments) this.addWideEdgeVisual(child, definition);
      });
      group.add(edgeInstance);
    }
    return group;
  }

  private async loadGlb(uri: string): Promise<THREE.Object3D> {
    if (!this.currentFiles) throw new ScenePackageError('Scene package is not loaded');
    const bytes = bytesFor(this.currentFiles, uri);
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const gltf = await this.loader.parseAsync(buffer, '');
    return gltf.scene;
  }

  private materialFor(definition: Definition): THREE.MeshStandardMaterial {
    const appearance = definition.appearance_id ? this.appearances.get(definition.appearance_id) : undefined;
    return materialFromAppearance(appearance);
  }

  private edgeMaterialFor(definition: Definition): LineMaterial {
    const appearance = definition.appearance_id ? this.appearances.get(definition.appearance_id) : undefined;
    const baseColor = appearance?.base_color ?? [0.72, 0.75, 0.78, 1];
    const hsl = { h: 0, s: 0, l: 0 };
    new THREE.Color(baseColor[0], baseColor[1], baseColor[2]).getHSL(hsl);
    const material = new LineMaterial({
      color: new THREE.Color().setHSL(hsl.h, hsl.s, (hsl.l + CAD_EDGE_LIGHTNESS_OFFSET) % 1),
      linewidth: CAD_EDGE_LINE_WIDTH,
      worldUnits: false,
      transparent: baseColor[3] < 1,
      opacity: baseColor[3],
      depthTest: true,
      depthWrite: false,
    });
    material.resolution.set(this.renderer.domElement.clientWidth, this.renderer.domElement.clientHeight);
    return material;
  }

  private addWideEdgeVisual(source: THREE.LineSegments, definition: Definition): void {
    const position = source.geometry.getAttribute('position');
    const index = source.geometry.index;
    const indexCount = index?.count ?? position.count;
    const positions: number[] = [];
    for (let offset = 0; offset + 1 < indexCount; offset += 2) {
      const a = index ? index.getX(offset) : offset;
      const b = index ? index.getX(offset + 1) : offset + 1;
      positions.push(position.getX(a), position.getY(a), position.getZ(a), position.getX(b), position.getY(b), position.getZ(b));
    }
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(positions);
    const visual = new LineSegments2(geometry, this.edgeMaterialFor(definition));
    visual.name = 'cad-edge-visual';
    visual.userData.pickable = false;
    source.add(visual);
    const pickingMaterial = new THREE.LineBasicMaterial();
    pickingMaterial.visible = false;
    source.material = pickingMaterial;
  }

  private frame(root: THREE.Object3D = this.modelRoot): void {
    const box = new THREE.Box3().setFromObject(root);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() * 0.5, 0.01);
    const direction = new THREE.Vector3(1, 0.78, 1).normalize();
    this.camera.position.copy(center).add(direction.multiplyScalar(radius * 2.4));
    this.camera.near = Math.max(radius / 100, 0.001);
    this.camera.far = Math.max(radius * 100, 10);
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.maxDistance = radius * 12;
    this.controls.update();
  }

  private resize(container: HTMLElement): void {
    const width = container.clientWidth;
    const height = Math.max(container.clientHeight, 1);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.modelRoot.traverse((object) => {
      if (object instanceof LineSegments2) object.material.resolution.set(width, height);
    });
    this.previewRoot.traverse((object) => {
      if (object instanceof LineSegments2) object.material.resolution.set(width, height);
    });
  }

  private clearModel(): void {
    this.clearSelection();
    const disposedGeometries = new Set<THREE.BufferGeometry>();
    const disposedMaterials = new Set<THREE.Material>();
    const dispose = (root: THREE.Object3D): void => {
      root.traverse((object) => {
        if (!(object instanceof THREE.Mesh || object instanceof THREE.LineSegments || object instanceof THREE.Points)) return;
        if (!disposedGeometries.has(object.geometry)) {
          object.geometry.dispose();
          disposedGeometries.add(object.geometry);
        }
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        for (const material of materials) {
          if (!disposedMaterials.has(material)) {
            material.dispose();
            disposedMaterials.add(material);
          }
        }
      });
    };
    for (const object of this.geometryCache.values()) dispose(object);
    for (const object of this.edgeCache.values()) dispose(object);
    const clearRoot = (root: THREE.Object3D): void => {
      while (root.children.length) {
        const child = root.children[0];
        dispose(child);
        root.remove(child);
      }
    };
    clearRoot(this.modelRoot);
    clearRoot(this.previewRoot);
    this.previewHasFramed = false;
    this.geometryCache.clear();
    this.edgeCache.clear();
    this.definitions.clear();
    this.appearances.clear();
    this.sceneNodes.clear();
    this.currentManifest = null;
    this.currentFiles = null;
  }

  private clearSelection(): void {
    if (this.selectionHelper) {
      this.scene.remove(this.selectionHelper);
      this.selectionHelper.geometry.dispose();
      this.selectionHelper.material.dispose();
      this.selectionHelper = null;
    }
    this.selectedNodeId = null;
  }

  private readonly handlePointerDown = (event: PointerEvent): void => {
    if (event.button === 0) this.pointerStart = { x: event.clientX, y: event.clientY };
  };

  private readonly handlePointerUp = (event: PointerEvent): void => {
    const start = this.pointerStart;
    this.pointerStart = null;
    if (event.button !== 0 || !start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4) return;
    const bounds = this.renderer.domElement.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    const pointer = new THREE.Vector2(
      ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
      -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(pointer, this.camera);
    const hit = raycaster.intersectObject(this.modelRoot, true)[0]?.object;
    let candidate: THREE.Object3D | null = hit ?? null;
    while (candidate && typeof candidate.userData.sceneNodeId !== 'string') candidate = candidate.parent;
    const nodeId = candidate?.userData.sceneNodeId;
    if (typeof nodeId === 'string' && this.selectNode(nodeId)) this.onNodeSelected(nodeId);
  };

  private clearPreview(): void {
    while (this.previewRoot.children.length) {
      const child = this.previewRoot.children[0];
      this.disposePreviewObject(child);
      this.previewRoot.remove(child);
    }
  }

  private disposePreviewObject(root: THREE.Object3D): void {
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    root.traverse((object) => {
      if (!(object instanceof THREE.Mesh || object instanceof THREE.LineSegments || object instanceof THREE.Points)) return;
      if (!geometries.has(object.geometry)) {
        object.geometry.dispose();
        geometries.add(object.geometry);
      }
      const objectMaterials = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of objectMaterials) {
        if (!materials.has(material)) {
          material.dispose();
          materials.add(material);
        }
      }
    });
  }
}

function bytesFor(files: PackageFiles, uri: string): Uint8Array {
  const value = files[uri];
  if (!value) throw new ScenePackageError(`Scene package member is missing: ${uri}`);
  return value;
}

function validateMemberName(name: string): void {
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$/.test(name) || name.split('/').some((segment) => !segment || segment === '.' || segment === '..')) {
    throw new ScenePackageError(`Invalid scene package member: ${name}`);
  }
}

function packageRecords(manifest: SceneManifest): PackageRecord[] {
  const records: PackageRecord[] = [];
  const addAsset = (asset: Asset, kind: 'geometry' | 'edges'): void => {
    if (asset.asset_id !== asset.content_hash) throw new ScenePackageError(`${kind} asset ID differs from its content hash`);
    const digest = /^sha256:([0-9a-f]{64})$/.exec(asset.content_hash)?.[1];
    if (!digest || asset.uri !== `${kind}/sha256-${digest}.glb`) throw new ScenePackageError(`Invalid content-addressed ${kind} asset URI`);
    records.push(asset);
  };
  for (const asset of manifest.geometry_assets) addAsset(asset, 'geometry');
  for (const asset of manifest.edge_assets) addAsset(asset, 'edges');
  for (const asset of manifest.entity_assets ?? []) records.push(asset);
  for (const source of [manifest.source, manifest.presentation_source]) {
    if (!source?.embedded_artifact_uri) continue;
    if (typeof source.embedded_artifact_byte_length !== 'number' || typeof source.artifact_hash !== 'string') throw new ScenePackageError('Embedded artifact integrity metadata is missing');
    records.push({ uri: source.embedded_artifact_uri, byte_length: source.embedded_artifact_byte_length, content_hash: source.artifact_hash });
    for (const sourceFile of source.source_files ?? []) records.push(sourceFile);
  }
  return records;
}

async function validatePackageMembers(files: PackageFiles, manifest: SceneManifest): Promise<void> {
  const records = packageRecords(manifest);
  const referenced = new Set<string>(['scene.json']);
  for (const record of records) {
    validateMemberName(record.uri);
    if (referenced.has(record.uri)) throw new ScenePackageError(`Duplicate scene package reference: ${record.uri}`);
    referenced.add(record.uri);
  }
  const names = Object.keys(files);
  for (const name of names) validateMemberName(name);
  if (names.length !== referenced.size || names.some((name) => !referenced.has(name))) throw new ScenePackageError('Scene package members do not match scene.json references');
  for (const record of records) {
    const payload = bytesFor(files, record.uri);
    if (!Number.isSafeInteger(record.byte_length) || record.byte_length < 0 || payload.byteLength !== record.byte_length) throw new ScenePackageError(`Package member length differs from scene.json: ${record.uri}`);
    const expected = /^sha256:([0-9a-f]{64})$/.exec(record.content_hash)?.[1];
    if (!expected) throw new ScenePackageError(`Invalid package member hash: ${record.uri}`);
    const digest = await sha256Hex(payload);
    if (digest !== expected) throw new ScenePackageError(`Package member hash differs from scene.json: ${record.uri}`);
  }
}

async function sha256Hex(payload: Uint8Array): Promise<string> {
  if (globalThis.crypto?.subtle) {
    return bytesToHex(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', payload)));
  }
  return bytesToHex(sha256(payload));
}

function unzipPackage(raw: Uint8Array): PackageFiles {
  if (raw.byteLength > MAX_PACKAGE_BYTES) throw new ScenePackageError('Scene package exceeds the browser size limit');
  let memberCount = 0;
  let unpackedBytes = 0;
  const seenNames = new Set<string>();
  unzipSync(raw, { filter: (entry) => {
    validateMemberName(entry.name);
    const foldedName = entry.name.toLowerCase();
    if (seenNames.has(foldedName)) throw new ScenePackageError(`Duplicate or case-colliding scene package member: ${entry.name}`);
    seenNames.add(foldedName);
    memberCount += 1;
    unpackedBytes += entry.originalSize;
    if (entry.compression !== 0 && entry.compression !== 8) throw new ScenePackageError(`Unsupported ZIP compression method: ${entry.name}`);
    if (memberCount > MAX_PACKAGE_MEMBERS) throw new ScenePackageError('Scene package member count is invalid');
    if (entry.originalSize > MAX_MEMBER_BYTES) throw new ScenePackageError(`Scene package member exceeds the browser size limit: ${entry.name}`);
    if (entry.name === 'scene.json' && entry.originalSize > MAX_SCENE_JSON_BYTES) throw new ScenePackageError('scene.json is missing or too large');
    if (unpackedBytes > MAX_UNPACKED_BYTES) throw new ScenePackageError('Scene package expands beyond the browser size limit');
    if (entry.originalSize > MAX_COMPRESSION_RATIO * Math.max(1, entry.size)) throw new ScenePackageError(`Scene package member compression ratio is too high: ${entry.name}`);
    return false;
  }});
  if (memberCount === 0 || !seenNames.has('scene.json')) throw new ScenePackageError('scene.json is missing or too large');
  if (unpackedBytes > MAX_COMPRESSION_RATIO * raw.byteLength) throw new ScenePackageError('Scene package compression ratio is too high');
  const files = unzipSync(raw) as PackageFiles;
  const entries = Object.entries(files);
  if (entries.length !== memberCount) throw new ScenePackageError('Scene package member count changed during extraction');
  const total = entries.reduce((sum, [, value]) => sum + value.byteLength, 0);
  if (total !== unpackedBytes) throw new ScenePackageError('Scene package decoded size differs from ZIP metadata');
  if (bytesFor(files, 'scene.json').byteLength > MAX_SCENE_JSON_BYTES) throw new ScenePackageError('scene.json is missing or too large');
  return files;
}

function applyAssetTransform(object: THREE.Object3D, matrix: number[]): void {
  if (matrix.length !== 16) throw new ScenePackageError('asset_to_scene must contain 16 values');
  object.applyMatrix4(new THREE.Matrix4().set(
    matrix[0] / 1000, matrix[1] / 1000, matrix[2] / 1000, matrix[3] / 1000,
    matrix[4] / 1000, matrix[5] / 1000, matrix[6] / 1000, matrix[7] / 1000,
    matrix[8] / 1000, matrix[9] / 1000, matrix[10] / 1000, matrix[11] / 1000,
    matrix[12], matrix[13], matrix[14], matrix[15],
  ));
}

function placementMatrix(transform: Transform): THREE.Matrix4 {
  const { origin, x_axis, y_axis, z_axis } = transform;
  return new THREE.Matrix4().set(
    x_axis[0], y_axis[0], z_axis[0], origin[0] / 1000,
    x_axis[1], y_axis[1], z_axis[1], origin[1] / 1000,
    x_axis[2], y_axis[2], z_axis[2], origin[2] / 1000,
    0, 0, 0, 1,
  );
}
