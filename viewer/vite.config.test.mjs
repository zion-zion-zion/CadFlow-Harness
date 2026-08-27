import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { createServer } from 'vite';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));
const projectId = '00000000000000000000000000000000';

test('trace routes use the trace entry in the development server', async () => {
  const server = await createServer({
    root: viewerRoot,
    logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0 },
  });
  await server.listen();

  try {
    const address = server.httpServer?.address();
    assert(address && typeof address !== 'string');
    const origin = `http://127.0.0.1:${address.port}`;

    for (const path of ['/trace', '/trace/', `/trace/${projectId}`]) {
      const response = await fetch(`${origin}${path}`, {
        headers: { Accept: 'text/html' },
      });
      const html = await response.text();

      assert.equal(response.status, 200, path);
      assert.match(html, /src="\/src\/trace\.ts"/, path);
      assert.doesNotMatch(html, /src="\/src\/main\.ts"/, path);
    }

    const workspaceHtml = await fetch(origin, {
      headers: { Accept: 'text/html' },
    }).then((response) => response.text());
    assert.match(workspaceHtml, /src="\/src\/main\.ts"/);
  } finally {
    await server.close();
  }
});
