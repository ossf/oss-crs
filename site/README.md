# OSS-CRS site

Docusaurus site for OSS-CRS.

- **Docs** are read directly from `../docs/` — no duplication.
- **Registry** page is built from `../registry/*.yaml` (and `../registry/*/pkg.yaml`) by a custom Docusaurus plugin (`plugins/registry-loader`).
- **Landing page** highlights the Quick Start, Registry, and Docs.

## Develop

```bash
cd site
npm install
npm run start         # dev server with hot reload
```

## Build

```bash
npm run build         # static site under site/build/
npm run serve         # serve the built site locally
```

## How the pieces connect

| Surface | Source |
|---|---|
| `/` (landing) | `src/pages/index.js` |
| `/docs/**` | `../docs/**` via `docusaurus.config.js` `presets.docs.path` |
| `/registry` | `src/pages/registry.js` + `plugins/registry-loader` over `../registry/` |
| Sidebar | `sidebars.js` |
| Theme | `src/css/custom.css` |
