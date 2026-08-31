import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));
const server = await createServer({ root: viewerRoot, server: { middlewareMode: true }, logLevel: 'silent' });
const { parseApiError, request, requestBinary, requestArrayBuffer } = await server.ssrLoadModule('/src/api.ts');

test.after(async () => { await server.close(); });

test('API helpers preserve structured errors and binary failure labels', async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'No Project' }), { status: 404 });
    await assert.rejects(() => request('/api/projects/missing'), /No Project/);
    assert.equal(parseApiError('plain text', 502), 'Request failed (502)');
    globalThis.fetch = async () => new Response('', { status: 404 });
    await assert.rejects(() => requestBinary('/scene', undefined, (status) => `Scene Artifact request failed (${status})`), /Scene Artifact request failed \(404\)/);
    globalThis.fetch = async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 });
    const payload = await requestArrayBuffer('/preview');
    assert.deepEqual([...new Uint8Array(payload)], [1, 2, 3]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
