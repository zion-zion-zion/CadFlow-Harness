export type ProductItemKind = 'part' | 'assembly';

export type ProductComponent = {
  component_id: string;
  item_kind: ProductItemKind;
  item_id: string;
};

export type ProductAssemblyDefinition = {
  assembly_id: string;
  components: ProductComponent[];
};

export type ProductPartDefinition = {
  part_id: string;
  name: string | null;
  material: string | null;
};

export type ProductSemanticModel = {
  root: { item_kind: ProductItemKind; item_id: string };
  assembly_definitions: ProductAssemblyDefinition[];
  part_definitions: ProductPartDefinition[];
};

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
