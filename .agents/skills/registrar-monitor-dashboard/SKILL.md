---
name: registrar-monitor-dashboard
description: Build, debug, verify, or deploy the Registrar Monitor generated Vite dashboard, immutable static data, course routes, preview images, or Cloudflare Pages publication.
---

# Registrar Monitor dashboard

Read the website publication and tooling sections of `docs/operations.md`.

## Rules

- Source is TypeScript modules in `frontend/src/` (`state.ts`, `chart.ts`, `modal.ts`,
  `routing.ts`, `search.ts`, ...) behind the `frontend/src/main.js` Vite entry; shared
  mutable state lives in `appState`. `npm --prefix frontend run typecheck` must pass.

- Generate and test `frontend/public`, not Vite's source shell.
- Publish static v3 data through no-cache pointers and immutable content-addressed
  assets.
- Deploy Pages by direct upload through `WebsiteService`. The preview-image Worker
  is separate.
- A successful build or upload log is not deployed-asset verification. Validate
  the hashed asset referenced by the generated manifest and hard-refresh.

## Worker

`workers/preview-images/` is a separate Cloudflare Worker serving `/preview/*`. Run
`make worker-check` (types, vitest, `tsc`, deploy dry-run) after changing it. Deploy it
only with explicit authorization, using the commands in `docs/operations.md`.

## Verification

Run the closest frontend unit test (`node --test frontend/test/<name>.test.mjs`) and build. Use generated-site smoke or browser
tests only when behavior or publication output changed. Production upload requires
explicit authorization.
