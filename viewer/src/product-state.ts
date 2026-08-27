export type ProductItemKind = 'part' | 'assembly';

export type ProductVec3 = [number, number, number];

export type ProductPlacement = {
  origin: ProductVec3;
  x_axis: ProductVec3;
  y_axis: ProductVec3;
  z_axis: ProductVec3;
};

export type ProductConnector = {
  connector_id: string;
  name?: string | null;
  anchor?: {
    anchor_kind?: string;
    placement?: ProductPlacement;
  };
};

export type ProductConnectorRef = {
  component_id: string;
  connector_id: string;
};

export type ProductScalarLimit = {
  lower_value?: number;
  upper_value?: number;
  lower?: number;
  upper?: number;
  min?: number;
  max?: number;
};

export type ProductConstraint = {
  constraint_id: string;
  constraint_kind: string;
  connector_a: ProductConnectorRef;
  connector_b: ProductConnectorRef;
  name?: string | null;
  drive_angle_degrees?: number | null;
  angle_limit?: ProductScalarLimit | [number, number] | null;
  pitch_radius_a?: number | null;
  pitch_radius_b?: number | null;
  pulley_radius_a?: number | null;
  pulley_radius_b?: number | null;
  phase_offset?: number | null;
};

export type ProductComponent = {
  component_id: string;
  item_kind: ProductItemKind;
  item_id: string;
  name?: string | null;
  placement?: ProductPlacement;
};

export type ProductAssemblyDefinition = {
  assembly_id: string;
  components: ProductComponent[];
  connectors?: ProductConnector[];
  constraints?: ProductConstraint[];
  grounded_component_ids?: string[];
};

export type ProductPartDefinition = {
  part_id: string;
  name: string | null;
  material: string | null;
  connectors?: ProductConnector[];
};

export type ProductSemanticModel = {
  root: { item_kind: ProductItemKind; item_id: string };
  assembly_definitions: ProductAssemblyDefinition[];
  part_definitions: ProductPartDefinition[];
};

export type ProductMotionJoint = {
  joint_id: string;
  label: string;
  assembly_id: string;
  moving_component_path: string;
  moving_group_paths: string[];
  moving_item_id: string;
  moving_item_kind: ProductItemKind;
  connector_id: string;
  connector_placement: ProductPlacement | null;
  initial_angle_degrees: number;
  lower_angle_degrees: number;
  upper_angle_degrees: number;
};

export type ProductMotionCoupling = {
  coupling_id: string;
  kind: 'gear' | 'belt';
  joint_a_id: string;
  joint_b_id: string;
  ratio: number;
  phase_offset_degrees: number;
};

export type ProductMotionModel = {
  joints: ProductMotionJoint[];
  couplings: ProductMotionCoupling[];
};

type MotionAssemblyOccurrence = {
  definition: ProductAssemblyDefinition;
  path: string;
  components: Map<string, ProductComponent>;
  componentPaths: Map<string, string>;
};

type MotionUnionFind = {
  find(value: string): string;
  union(left: string, right: string): void;
};

export function buildMotionModel(model: ProductSemanticModel): ProductMotionModel {
  const assemblies = new Map(
    model.assembly_definitions.map((definition) => [definition.assembly_id, definition]),
  );
  const parts = new Map(
    model.part_definitions.map((definition) => [definition.part_id, definition]),
  );
  const occurrences: MotionAssemblyOccurrence[] = [];
  const visit = (assemblyId: string, path: string, ancestors: ReadonlySet<string>): void => {
    if (ancestors.has(assemblyId)) return;
    const definition = assemblies.get(assemblyId);
    if (!definition) return;
    const components = new Map(definition.components.map((component) => [component.component_id, component]));
    const componentPaths = new Map(
      definition.components.map((component) => [component.component_id, `${path}/${component.component_id}`]),
    );
    occurrences.push({ definition, path, components, componentPaths });
    const nextAncestors = new Set(ancestors);
    nextAncestors.add(assemblyId);
    for (const component of definition.components) {
      if (component.item_kind === 'assembly') visit(component.item_id, `${path}/${component.component_id}`, nextAncestors);
    }
  };
  if (model.root.item_kind === 'assembly') visit(model.root.item_id, model.root.item_id, new Set());

  const joints: ProductMotionJoint[] = [];
  const jointByComponentPath = new Map<string, string>();
  const jointByGroupKey = new Set<string>();
  const couplingInputs: Array<{ constraint: ProductConstraint; occurrence: MotionAssemblyOccurrence }> = [];
  for (const occurrence of occurrences) {
    const constraints = occurrence.definition.constraints ?? [];
    for (const constraint of constraints) {
      if (constraint.constraint_kind === 'gear' || constraint.constraint_kind === 'belt') {
        couplingInputs.push({ constraint, occurrence });
        continue;
      }
      if (constraint.constraint_kind !== 'revolute') continue;
      const left = occurrence.components.get(constraint.connector_a.component_id);
      const right = occurrence.components.get(constraint.connector_b.component_id);
      if (!left || !right) continue;
      const union = makeMotionUnionFind(occurrence.components.keys());
      for (const fixed of constraints) {
        if (fixed.constraint_kind !== 'fixed') continue;
        if (!occurrence.components.has(fixed.connector_a.component_id) || !occurrence.components.has(fixed.connector_b.component_id)) continue;
        union.union(fixed.connector_a.component_id, fixed.connector_b.component_id);
      }
      const grounded = new Set(occurrence.definition.grounded_component_ids ?? []);
      const groundedGroups = new Set([...grounded].map((componentId) => union.find(componentId)));
      const leftGrounded = groundedGroups.has(union.find(left.component_id));
      const rightGrounded = groundedGroups.has(union.find(right.component_id));
      const moving = leftGrounded && !rightGrounded ? right : rightGrounded && !leftGrounded ? left : right;
      const movingRef = moving === left ? constraint.connector_a : constraint.connector_b;
      const movingGroup = [...occurrence.components.keys()]
        .filter((componentId) => union.find(componentId) === union.find(moving.component_id))
        .map((componentId) => occurrence.componentPaths.get(componentId))
        .filter((path): path is string => Boolean(path));
      const part = moving.item_kind === 'part' ? parts.get(moving.item_id) : undefined;
      const assembly = moving.item_kind === 'assembly' ? assemblies.get(moving.item_id) : undefined;
      const connector = [...(part?.connectors ?? []), ...(assembly?.connectors ?? [])]
        .find((candidate) => candidate.connector_id === movingRef.connector_id);
      const limit = scalarLimitValues(constraint.angle_limit);
      const initial = finiteOr(constraint.drive_angle_degrees, 0);
      const joint: ProductMotionJoint = {
        joint_id: constraint.constraint_id,
        label: constraint.name || constraint.constraint_id,
        assembly_id: occurrence.definition.assembly_id,
        moving_component_path: occurrence.componentPaths.get(moving.component_id) ?? `${occurrence.path}/${moving.component_id}`,
        moving_group_paths: movingGroup,
        moving_item_id: moving.item_id,
        moving_item_kind: moving.item_kind,
        connector_id: movingRef.connector_id,
        connector_placement: connector?.anchor?.placement ?? null,
        initial_angle_degrees: initial,
        lower_angle_degrees: limit?.[0] ?? -180,
        upper_angle_degrees: limit?.[1] ?? 180,
      };
      const groupKey = movingGroup.join('|');
      if (jointByGroupKey.has(groupKey)) continue;
      jointByGroupKey.add(groupKey);
      joints.push(joint);
      for (const path of movingGroup) {
        if (!jointByComponentPath.has(path)) jointByComponentPath.set(path, joint.joint_id);
      }
    }
  }

  const couplings: ProductMotionCoupling[] = [];
  for (const { constraint, occurrence } of couplingInputs) {
    const leftPath = occurrence.componentPaths.get(constraint.connector_a.component_id);
    const rightPath = occurrence.componentPaths.get(constraint.connector_b.component_id);
    if (!leftPath || !rightPath) continue;
    const jointA = jointByComponentPath.get(leftPath);
    const jointB = jointByComponentPath.get(rightPath);
    if (!jointA || !jointB || jointA === jointB) continue;
    const radiusA = constraint.constraint_kind === 'gear' ? constraint.pitch_radius_a : constraint.pulley_radius_a;
    const radiusB = constraint.constraint_kind === 'gear' ? constraint.pitch_radius_b : constraint.pulley_radius_b;
    if (!Number.isFinite(radiusA) || !Number.isFinite(radiusB) || radiusA === 0 || radiusB === 0) continue;
    const kind = constraint.constraint_kind === 'gear' ? 'gear' : 'belt';
    couplings.push({
      coupling_id: constraint.constraint_id,
      kind,
      joint_a_id: jointA,
      joint_b_id: jointB,
      ratio: (constraint.constraint_kind === 'gear' ? -1 : 1) * Number(radiusA) / Number(radiusB),
      phase_offset_degrees: finiteOr(constraint.phase_offset, 0),
    });
  }
  return { joints, couplings };
}

function makeMotionUnionFind(values: Iterable<string>): MotionUnionFind {
  const parent = new Map<string, string>();
  for (const value of values) parent.set(value, value);
  const find = (value: string): string => {
    const current = parent.get(value) ?? value;
    if (current === value) return current;
    const root = find(current);
    parent.set(value, root);
    return root;
  };
  return {
    find,
    union(left, right) {
      const leftRoot = find(left);
      const rightRoot = find(right);
      if (leftRoot !== rightRoot) parent.set(rightRoot, leftRoot);
    },
  };
}

function finiteOr(value: number | null | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function scalarLimitValues(value: ProductConstraint['angle_limit']): [number, number] | null {
  if (Array.isArray(value) && value.length === 2 && value.every((item) => Number.isFinite(item))) {
    return [Math.min(value[0], value[1]), Math.max(value[0], value[1])];
  }
  if (!value || Array.isArray(value)) return null;
  const lower = value.lower_value ?? value.lower ?? value.min;
  const upper = value.upper_value ?? value.upper ?? value.max;
  if (!Number.isFinite(lower) || !Number.isFinite(upper)) return null;
  return [Math.min(Number(lower), Number(upper)), Math.max(Number(lower), Number(upper))];
}

export type ProductTreeNode = {
  key: string;
  path: string;
  sceneNodeId: string;
  label: string;
  itemKind: ProductItemKind;
  itemId: string;
  children: ProductTreeNode[];
};

export function sceneNodeIdForProductPath(path: string): string {
  const segments = path.split('/').filter(Boolean);
  return ['instance', 'main', ...segments.slice(1)].join('/');
}

export function buildProductTree(model: ProductSemanticModel): ProductTreeNode {
  const assemblies = new Map(
    model.assembly_definitions.map((definition) => [definition.assembly_id, definition]),
  );
  const parts = new Map(
    model.part_definitions.map((definition) => [definition.part_id, definition]),
  );

  const build = (
    itemKind: ProductItemKind,
    itemId: string,
    path: string,
    componentLabel?: string,
    ancestors: ReadonlySet<string> = new Set(),
  ): ProductTreeNode => {
    if (itemKind === 'part') {
      const part = parts.get(itemId);
      return {
        key: path,
        path,
        sceneNodeId: sceneNodeIdForProductPath(path),
        label: componentLabel || part?.name || itemId,
        itemKind,
        itemId,
        children: [],
      };
    }

    if (ancestors.has(itemId)) throw new Error(`Cyclic Assembly definition: ${itemId}`);
    const assembly = assemblies.get(itemId);
    if (!assembly) throw new Error(`Missing Assembly definition: ${itemId}`);
    const nextAncestors = new Set(ancestors);
    nextAncestors.add(itemId);
    return {
      key: path,
      path,
      sceneNodeId: sceneNodeIdForProductPath(path),
      label: componentLabel || itemId,
      itemKind,
      itemId,
      children: assembly.components.map((component) => build(
        component.item_kind,
        component.item_id,
        `${path}/${component.component_id}`,
        component.component_id,
        nextAncestors,
      )),
    };
  };

  return build(model.root.item_kind, model.root.item_id, model.root.item_id);
}

export function flattenProductTree(root: ProductTreeNode): ProductTreeNode[] {
  const result: ProductTreeNode[] = [];
  const visit = (node: ProductTreeNode): void => {
    result.push(node);
    node.children.forEach(visit);
  };
  visit(root);
  return result;
}

export function nodeIsInIsolation(nodeId: string, isolatedNodeId: string): boolean {
  return nodeId === isolatedNodeId
    || nodeId.startsWith(`${isolatedNodeId}/`)
    || isolatedNodeId.startsWith(`${nodeId}/`);
}
