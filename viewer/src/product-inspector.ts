import { buildProductTree, flattenProductTree, type ProductMotionJoint, type ProductSemanticModel, type ProductTreeNode } from './product-state';
import type { ProductResponse, Project } from './api';
import { request } from './api';
import { humanizeIdentifier } from './formatters';
import type { ProjectSession } from './project-session';
import type { ShellElements } from './shell';
import type { SceneViewer } from './components/scene-viewer';

type ProductElements = Pick<ShellElements, 'productInspector' | 'productTitle' | 'productSummary' | 'productStatus' | 'productDownloads' | 'productDownloadList' | 'productPane' | 'productTabButtons'>;
export type ProductTab = 'structure' | 'bom' | 'validation' | 'motion';

export class ProductInspector {
  private acceptedProduct: ProductResponse | null = null;
  private acceptedProductTree: ProductTreeNode | null = null;
  private productLoading = false;
  private productLoadError = '';
  private activeProductTab: ProductTab = 'structure';
  private selectedProductNodeKey: string | null = null;

  constructor(
    private readonly elements: ProductElements,
    private readonly sceneViewer: SceneViewer,
    private readonly session: ProjectSession,
    private readonly currentProject: () => Project | null,
    private readonly selectedProjectId: () => string | null,
  ) {}

  get product(): ProductResponse | null { return this.acceptedProduct; }
  get semanticModel(): ProductSemanticModel | null { return this.acceptedProduct?.semantic_model ?? null; }
  get loadedProductProjectId(): string | null { return this.session.loadedProductProjectId; }
  get loadedProductArtifactVersion(): number | null { return this.session.loadedProductArtifactVersion; }

  reset(resetTab = false): void {
    this.session.cancelProductRequest();
    this.session.loadedProductProjectId = null;
    this.session.loadedProductArtifactVersion = null;
    this.acceptedProduct = null; this.acceptedProductTree = null;
    this.productLoading = false; this.productLoadError = ''; this.selectedProductNodeKey = null;
    this.sceneViewer.setMotionModel(null);
    this.elements.productDownloads.open = false;
    if (resetTab) this.activeProductTab = 'structure';
  }

  shouldLoad(project: Project): boolean {
    return project.product_available && project.state !== 'Running' && (this.session.loadedProductProjectId !== project.project_id || this.session.loadedProductArtifactVersion !== project.artifact_version);
  }

  async load(project: Project, version: number): Promise<void> {
    if (!project.product_available || project.state === 'Running' || !this.session.isCurrent(project.project_id, version)) return;
    const controller = this.session.beginProductRequest();
    this.session.loadedProductProjectId = null; this.session.loadedProductArtifactVersion = null; this.acceptedProduct = null; this.acceptedProductTree = null; this.selectedProductNodeKey = null; this.productLoadError = ''; this.productLoading = true;
    this.render();
    try {
      const product = await request<ProductResponse>(`/api/projects/${encodeURIComponent(project.project_id)}/product`, { signal: controller.signal });
      if (!this.session.isCurrent(project.project_id, version)) return;
      if (product.schema_version !== 'cadflow-product-api/v1' || product.status !== 'Accepted') throw new Error('Product API returned an unsupported record.');
      if (!product.semantic_model) throw new Error('Accepted product has no semantic model.');
      this.acceptedProductTree = buildProductTree(product.semantic_model);
      this.acceptedProduct = product;
      this.sceneViewer.setMotionModel(product.semantic_model);
      this.session.loadedProductProjectId = project.project_id;
      this.session.loadedProductArtifactVersion = project.artifact_version;
    } catch (error) {
      if (controller.signal.aborted || !this.session.isCurrent(project.project_id, version)) return;
      this.productLoadError = error instanceof Error ? error.message : 'The request could not be completed.';
    } finally {
      this.session.finishProductRequest(controller);
      if (this.session.isCurrent(project.project_id, version)) { this.productLoading = false; this.render(); }
    }
  }

  render(): void {
    const project = this.currentProject();
    const visible = project !== null && project.product_available && project.state !== 'Running';
    this.elements.productInspector.hidden = !visible;
    if (!visible || !project) return;
    this.elements.productTitle.textContent = project.result_kind === 'assembly' ? 'Assembly' : 'Part';
    this.elements.productStatus.textContent = project.product_status ?? 'Accepted';
    this.elements.productSummary.textContent = this.acceptedProduct ? `${this.acceptedProduct.summary.unique_part_count} unique · ${this.acceptedProduct.summary.leaf_part_count} instance${this.acceptedProduct.summary.leaf_part_count === 1 ? '' : 's'}` : 'Loading product record';
    this.renderDownloads();
    for (const button of this.elements.productTabButtons) {
      const selected = button.dataset.productTab === this.activeProductTab;
      button.classList.toggle('selected', selected); button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
    }
    this.elements.productPane.replaceChildren();
    this.elements.productPane.setAttribute('aria-label', humanizeIdentifier(this.activeProductTab));
    if (this.productLoading && !this.acceptedProduct) { this.appendMessage('Loading accepted product...'); return; }
    if (this.productLoadError) { this.appendMessage(this.productLoadError, true); return; }
    if (!this.acceptedProduct) { this.appendMessage('Accepted product data is unavailable.', true); return; }
    if (this.activeProductTab === 'structure') this.renderStructure();
    else if (this.activeProductTab === 'bom') this.renderBom();
    else if (this.activeProductTab === 'validation') this.renderValidation();
    else this.renderMotion();
  }

  setTab(tab: ProductTab): void { this.activeProductTab = tab; this.render(); }
  selectNodeBySceneId(nodeId: string): void {
    const node = this.acceptedProductTree ? flattenProductTree(this.acceptedProductTree).find((item) => item.sceneNodeId === nodeId) : undefined;
    if (node) { this.selectedProductNodeKey = node.key; this.render(); }
  }

  private renderDownloads(): void {
    const { productDownloads, productDownloadList } = this.elements;
    productDownloadList.replaceChildren();
    if (!this.acceptedProduct) { productDownloads.hidden = true; productDownloads.open = false; return; }
    productDownloads.hidden = false;
    const labels: Record<string, string> = { product_step: 'Product STEP', scene: 'Scene', source_snapshot: 'Source snapshot', semantic_model: 'Semantic model', bom: 'BOM', validation_report: 'Validation report', assumptions: 'Assumptions' };
    const manifest = document.createElement('a'); manifest.href = this.acceptedProduct.manifest_url; manifest.download = ''; manifest.textContent = 'Manifest'; productDownloadList.append(manifest);
    for (const [role, file] of Object.entries(this.acceptedProduct.files)) { const link = document.createElement('a'); link.href = file.download_url; link.download = ''; link.textContent = labels[role] ?? humanizeIdentifier(role); productDownloadList.append(link); }
  }
  private appendMessage(message: string, error = false): void { const element = document.createElement('p'); element.className = `product-message${error ? ' error' : ''}`; element.textContent = message; this.elements.productPane.append(element); }
  private renderMotion(): void {
    const joints = this.sceneViewer.motionJoints();
    if (joints.length === 0) { this.appendMessage('No interactive revolute joints are available for this product.'); return; }
    const toolbar = document.createElement('div'); toolbar.className = 'product-toolbar motion-toolbar';
    const play = document.createElement('button'); play.type = 'button'; play.className = 'inspector-button'; play.textContent = this.sceneViewer.isMotionPlaying() ? 'Pause' : 'Play'; play.addEventListener('click', () => { this.sceneViewer.setMotionPlaying(!this.sceneViewer.isMotionPlaying(), joints[0].joint_id); this.render(); });
    const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'inspector-button'; reset.textContent = 'Reset'; reset.addEventListener('click', () => { this.sceneViewer.setMotionPlaying(false); this.sceneViewer.resetMotion(); this.render(); });
    toolbar.append(play, reset); const hint = document.createElement('p'); hint.className = 'product-message'; hint.textContent = 'Adjust a joint angle to preview its connected parts and couplings.'; this.elements.productPane.append(toolbar, hint); joints.forEach((joint) => this.appendMotionJoint(joint));
  }
  private appendMotionJoint(joint: ProductMotionJoint): void {
    const field = document.createElement('label'); field.className = 'motion-joint';
    const heading = document.createElement('span'); heading.className = 'motion-joint-heading'; const name = document.createElement('strong'); name.textContent = joint.label; const value = document.createElement('output'); value.textContent = `${Math.round(this.sceneViewer.jointAngle(joint.joint_id) ?? joint.initial_angle_degrees)}°`; heading.append(name, value);
    const slider = document.createElement('input'); slider.type = 'range'; slider.min = String(joint.lower_angle_degrees); slider.max = String(joint.upper_angle_degrees); slider.step = '1'; slider.value = String(this.sceneViewer.jointAngle(joint.joint_id) ?? joint.initial_angle_degrees); slider.addEventListener('input', () => { this.sceneViewer.setMotionPlaying(false); this.sceneViewer.setJointAngle(joint.joint_id, Number(slider.value)); this.updateMotionControls(); }); slider.dataset.jointId = joint.joint_id; field.dataset.jointId = joint.joint_id; field.append(heading, slider); this.elements.productPane.append(field);
  }
  updateMotionControls(): void {
    for (const row of this.elements.productPane.querySelectorAll<HTMLElement>('.motion-joint')) { const jointId = row.dataset.jointId; if (!jointId) continue; const angle = this.sceneViewer.jointAngle(jointId); if (angle === null) continue; const slider = row.querySelector<HTMLInputElement>('input[type="range"]'); const output = row.querySelector<HTMLOutputElement>('output'); if (slider) slider.value = String(angle); if (output) output.textContent = `${Math.round(angle)}°`; }
  }
  private renderStructure(): void {
    if (!this.acceptedProductTree) { this.appendMessage('Semantic product structure is unavailable.', true); return; }
    const sceneReady = this.session.loadedSceneProjectId === this.selectedProjectId();
    const toolbar = document.createElement('div'); toolbar.className = 'product-toolbar'; const count = document.createElement('span'); count.textContent = `${this.acceptedProduct?.summary.component_count ?? 0} components`; const showAll = document.createElement('button'); showAll.type = 'button'; showAll.className = 'inspector-button'; showAll.textContent = 'Show All'; showAll.disabled = !sceneReady; showAll.addEventListener('click', () => { this.sceneViewer.showAll(); this.render(); }); toolbar.append(count, showAll);
    const tree = document.createElement('div'); tree.className = 'product-tree'; tree.setAttribute('role', 'tree'); this.appendTreeNode(tree, this.acceptedProductTree, 1, sceneReady); this.elements.productPane.append(toolbar, tree);
  }
  private appendTreeNode(tree: HTMLElement, node: ProductTreeNode, level: number, sceneReady: boolean): void {
    const row = document.createElement('div'); row.className = 'product-tree-row'; row.classList.toggle('selected', this.selectedProductNodeKey === node.key); row.style.setProperty('--tree-depth', String(level - 1)); row.setAttribute('role', 'treeitem'); row.setAttribute('aria-level', String(level)); row.setAttribute('aria-selected', String(this.selectedProductNodeKey === node.key));
    const select = document.createElement('button'); select.type = 'button'; select.className = 'product-tree-select'; select.disabled = !sceneReady || !this.sceneViewer.hasNode(node.sceneNodeId); select.title = node.path; const kind = document.createElement('span'); kind.className = `tree-kind tree-kind-${node.itemKind}`; kind.textContent = node.itemKind === 'assembly' ? 'A' : 'P'; const copy = document.createElement('span'); copy.className = 'tree-copy'; const label = document.createElement('strong'); label.textContent = node.label; const identity = document.createElement('small'); identity.textContent = node.itemId; copy.append(label, identity); select.append(kind, copy); select.addEventListener('click', () => { this.selectedProductNodeKey = node.key; this.sceneViewer.selectNode(node.sceneNodeId); this.render(); });
    const visible = this.sceneViewer.isNodeVisible(node.sceneNodeId); const visibility = document.createElement('button'); visibility.type = 'button'; visibility.className = 'tree-action'; visibility.textContent = visible ? 'Hide' : 'Show'; visibility.title = `${visible ? 'Hide' : 'Show'} ${node.label}`; visibility.disabled = !sceneReady || !this.sceneViewer.hasNode(node.sceneNodeId); visibility.addEventListener('click', () => { this.sceneViewer.setNodeVisible(node.sceneNodeId, !visible); if (visible && this.selectedProductNodeKey !== null && (this.selectedProductNodeKey === node.key || this.selectedProductNodeKey.startsWith(`${node.key}/`))) this.selectedProductNodeKey = null; this.render(); });
    const isolate = document.createElement('button'); isolate.type = 'button'; isolate.className = 'tree-action'; isolate.textContent = 'Only'; isolate.title = `Isolate ${node.label}`; isolate.disabled = !sceneReady || !this.sceneViewer.hasNode(node.sceneNodeId); isolate.addEventListener('click', () => { this.selectedProductNodeKey = node.key; this.sceneViewer.isolateNode(node.sceneNodeId); this.render(); }); row.append(select, visibility, isolate); tree.append(row); for (const child of node.children) this.appendTreeNode(tree, child, level + 1, sceneReady);
  }
  private renderBom(): void {
    if (!this.acceptedProduct || this.acceptedProduct.bom.length === 0) { this.appendMessage('No BOM items are available.', true); return; }
    const partDownloads = new Map(this.acceptedProduct.parts.map((part) => [part.part_id, part.download_url])); const scroller = document.createElement('div'); scroller.className = 'bom-scroller'; const table = document.createElement('table'); table.className = 'bom-table'; const head = document.createElement('thead'); head.innerHTML = '<tr><th>Qty</th><th>Part</th><th>Material</th><th>STEP</th></tr>'; const body = document.createElement('tbody');
    for (const item of this.acceptedProduct.bom) { const row = document.createElement('tr'); const quantity = document.createElement('td'); quantity.textContent = String(item.quantity); const part = document.createElement('td'); const name = document.createElement('strong'); name.textContent = item.name || item.part_id; const partId = document.createElement('code'); partId.textContent = item.part_id; const instances = document.createElement('details'); instances.className = 'bom-instances'; const instanceSummary = document.createElement('summary'); instanceSummary.textContent = `${item.component_paths.length} path${item.component_paths.length === 1 ? '' : 's'}`; const paths = document.createElement('ul'); for (const componentPath of item.component_paths) { const path = document.createElement('li'); path.textContent = componentPath; paths.append(path); } instances.append(instanceSummary, paths); part.append(name, partId, instances); const material = document.createElement('td'); material.textContent = item.material || 'Not specified'; const downloadCell = document.createElement('td'); const download = document.createElement('a'); download.className = 'step-download'; download.href = partDownloads.get(item.part_id) ?? '#'; download.download = ''; download.textContent = 'STEP'; downloadCell.append(download); row.append(quantity, part, material, downloadCell); body.append(row); }
    table.append(head, body); scroller.append(table); this.elements.productPane.append(scroller);
  }
  private renderValidation(): void {
    const product = this.acceptedProduct;
    const report = product?.validation_report; if (!report) { this.appendMessage('Validation report is unavailable.', true); return; }
    const summary = document.createElement('div'); summary.className = 'validation-summary'; const state = document.createElement('strong'); state.textContent = report.status; const checks = document.createElement('span'); checks.textContent = `${report.checks.length} checks · ${report.blocking_failures.length} blocking`; summary.append(state, checks); const list = document.createElement('div'); list.className = 'validation-list';
    for (const check of report.checks) { const detail = document.createElement('details'); detail.className = `validation-check validation-${check.status}`; const heading = document.createElement('summary'); const status = document.createElement('span'); status.className = 'validation-check-status'; status.textContent = check.status === 'not_applicable' ? 'N/A' : check.status; const name = document.createElement('strong'); name.textContent = humanizeIdentifier(check.check_id); heading.append(status, name); detail.append(heading); if (check.message || check.evidence !== undefined) { const evidence = document.createElement('pre'); evidence.textContent = [check.message, check.evidence === undefined ? null : JSON.stringify(check.evidence, null, 2)].filter((item): item is string => typeof item === 'string' && item.length > 0).join('\n\n'); detail.append(evidence); } list.append(detail); }
    this.elements.productPane.append(summary, list); const assumptionsHeading = document.createElement('h2'); assumptionsHeading.className = 'inspector-subheading'; assumptionsHeading.textContent = 'Assumptions'; const assumptions = document.createElement('ul'); assumptions.className = 'assumption-list'; for (const assumption of product.assumptions) { const item = document.createElement('li'); item.textContent = assumption; assumptions.append(item); } if (assumptions.children.length === 0) { const item = document.createElement('li'); item.textContent = 'None recorded'; assumptions.append(item); } this.elements.productPane.append(assumptionsHeading, assumptions);
  }
}
