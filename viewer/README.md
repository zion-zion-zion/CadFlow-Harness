# Local Text-to-CAD Project Workspace

The Vite app is the browser UI for the trusted local Text-to-CAD demo. It
loads the Project Catalog and Agent Run state from the FastAPI `/api` routes,
streams revision events while a run is active, and requests a canonical
`.scene.zip` only for a Succeeded Project.

The CAD Viewer keeps the Scene Schema 1.0 ZIP/member/hash checks, GLB loading,
automatic framing, Fit, and Three.js OrbitControls for rotate, pan, and zoom.
Live revisions arrive as bounded native CadFlow GLBs; the Viewer rejects stale
attempt/revision pairs and swaps a parsed frame only after it is ready.
It intentionally has no local ZIP picker, model tree, Inspector, entity
selection, source editor, or Provider credential controls.

## Run

Install the frontend dependencies and start the API separately:

```bash
npm ci
npm run dev
```

Vite serves on `127.0.0.1` and proxies `/api` to the FastAPI service at
`127.0.0.1:8000`; production builds are served by FastAPI from the same
origin when `viewer/dist` exists.
