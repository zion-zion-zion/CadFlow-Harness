import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { createServer } from 'vite';

const viewerRoot = fileURLToPath(new URL('.', import.meta.url));
const projectId = '00000000000000000000000000000000';

test('workspace shell includes the HUD copy target consumed during startup', async () => {
  const shellSource = await readFile(new URL('./src/shell.ts', import.meta.url), 'utf8');
  assert.match(shellSource, /id="viewer-hud-copy"/);
});

test('workspace sidebar has an accessible toggle and responsive collapsed layouts', async () => {
  const [shellSource, mainSource, styleSource] = await Promise.all([
    readFile(new URL('./src/shell.ts', import.meta.url), 'utf8'),
    readFile(new URL('./src/main.ts', import.meta.url), 'utf8'),
    readFile(new URL('./src/style.css', import.meta.url), 'utf8'),
  ]);

  assert.match(shellSource, /id="workspace-sidebar-toggle"[^>]+aria-expanded="true"[^>]+aria-controls="catalog-panel project-panel"/);
  assert.match(mainSource, /workspace\.classList\.toggle\('is-sidebar-collapsed'\)/);
  assert.match(mainSource, /workspaceSidebarToggle\.setAttribute\('aria-expanded', String\(expanded\)\)/);
  assert.match(mainSource, /matchMedia\('\(max-width: 900px\)'\)\.matches\) setControlRailExpanded\(false\)/);
  assert.match(styleSource, /\.workspace\.is-sidebar-collapsed\s*\{\s*grid-template-columns:\s*0 0 minmax\(0, 1fr\)/);
  assert.match(styleSource, /@media \(max-width: 900px\)[\s\S]+\.workspace\.is-sidebar-collapsed > \.panel\s*\{\s*display:\s*none/);
  assert.match(styleSource, /@media \(max-width: 900px\)[\s\S]+\.shell\s*\{[^}]+max-width:\s*100vw/);
  assert.match(styleSource, /\.viewer-control-rail\.is-collapsed\s*\{\s*right:\s*auto;\s*width:\s*50px/);
});

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
