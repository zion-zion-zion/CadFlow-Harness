import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));

async function loadProductState() {
  const server = await createServer({
    root: viewerRoot,
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  try {
    return await server.ssrLoadModule('/src/product-state.ts');
  } finally {
    await server.close();
  }
}

test('builds nested product paths and maps them to canonical Scene nodes', async () => {
  const { buildProductTree, flattenProductTree } = await loadProductState();
  const root = buildProductTree({
    root: { item_kind: 'assembly', item_id: 'drive' },
    assembly_definitions: [
      {
        assembly_id: 'drive',
        components: [
          { component_id: 'housing', item_kind: 'part', item_id: 'housing-part' },
          { component_id: 'stage-1', item_kind: 'assembly', item_id: 'planet-stage' },
        ],
      },
      {
        assembly_id: 'planet-stage',
        components: [
          { component_id: 'sun', item_kind: 'part', item_id: 'sun-gear' },
        ],
      },
    ],
    part_definitions: [
      { part_id: 'housing-part', name: 'Housing', material: null },
      { part_id: 'sun-gear', name: 'Sun Gear', material: 'Steel' },
    ],
  });

  assert.deepEqual(
    flattenProductTree(root).map((node) => [node.path, node.sceneNodeId]),
    [
      ['drive', 'instance/main'],
      ['drive/housing', 'instance/main/housing'],
      ['drive/stage-1', 'instance/main/stage-1'],
      ['drive/stage-1/sun', 'instance/main/stage-1/sun'],
    ],
  );
});

test('isolation keeps the target subtree and its ancestors visible', async () => {
  const { nodeIsInIsolation } = await loadProductState();
  const isolated = 'instance/main/stage-1';

  assert.equal(nodeIsInIsolation('instance/main', isolated), true);
  assert.equal(nodeIsInIsolation(isolated, isolated), true);
  assert.equal(nodeIsInIsolation('instance/main/stage-1/sun', isolated), true);
  assert.equal(nodeIsInIsolation('instance/main/housing', isolated), false);
});

test('derives revolute groups and gear ratios from semantic constraints', async () => {
  const { buildMotionModel } = await loadProductState();
  const model = buildMotionModel({
    root: { item_kind: 'assembly', item_id: 'drive' },
    assembly_definitions: [{
      assembly_id: 'drive',
      grounded_component_ids: ['housing'],
      components: [
        { component_id: 'housing', item_kind: 'part', item_id: 'housing-part' },
        { component_id: 'shaft', item_kind: 'part', item_id: 'shaft-part' },
        { component_id: 'gear', item_kind: 'part', item_id: 'gear-part' },
        { component_id: 'output', item_kind: 'part', item_id: 'output-part' },
      ],
      constraints: [
        {
          constraint_id: 'shaft_support',
          constraint_kind: 'revolute',
          connector_a: { component_id: 'housing', connector_id: 'axis' },
          connector_b: { component_id: 'shaft', connector_id: 'axis' },
        },
        {
          constraint_id: 'gear_rigid',
          constraint_kind: 'fixed',
          connector_a: { component_id: 'shaft', connector_id: 'mount' },
          connector_b: { component_id: 'gear', connector_id: 'axis' },
        },
        {
          constraint_id: 'gear_mesh',
          constraint_kind: 'gear',
          connector_a: { component_id: 'shaft', connector_id: 'axis' },
          connector_b: { component_id: 'output', connector_id: 'axis' },
          pitch_radius_a: 10,
          pitch_radius_b: 20,
        },
        {
          constraint_id: 'output_support',
          constraint_kind: 'revolute',
          connector_a: { component_id: 'housing', connector_id: 'axis2' },
          connector_b: { component_id: 'output', connector_id: 'axis' },
        },
      ],
    }],
    part_definitions: [
      { part_id: 'housing-part', name: 'Housing', material: null },
      { part_id: 'shaft-part', name: 'Shaft', material: null },
      { part_id: 'gear-part', name: 'Gear', material: null },
      { part_id: 'output-part', name: 'Output', material: null },
    ],
  });

  assert.equal(model.joints.length, 2);
  assert.deepEqual(model.joints[0].moving_group_paths, ['drive/shaft', 'drive/gear']);
  assert.equal(model.couplings.length, 1);
  assert.equal(model.couplings[0].ratio, -0.5);
});
