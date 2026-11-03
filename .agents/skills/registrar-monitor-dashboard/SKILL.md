---
name: registrar-monitor-dashboard
description: Build, debug, verify, or deploy the Registrar Monitor generated Vite dashboard, immutable static data, course routes, preview images, or Worker Static Assets publication.
---

# Registrar Monitor dashboard

Read the website publication and tooling sections of `docs/operations.md`.

## Rules

- Source is TypeScript modules in `frontend/src/` (`state.ts`, `chart.ts`, `modal.ts`,
  `routing.ts`, `search.ts`, `miniapp.ts`, ...) behind the `frontend/src/main.js` Vite entry; shared
  mutable state lives in `appState`. `npm --prefix frontend run typecheck` must pass.

- Telegram Mini App: `miniapp.ts` loads `telegram.org/js/telegram-web-app.js` only when
  the URL hash has `tgWebAppData`, and calls the bot API from the page's
  `miniapp-api` meta tag (`website.miniapp_api_url`). Keep pure logic in
  `miniappApi.mjs` with a node test. The CSP meta tag and `_headers` come from
  `website.config.content_security_policy` and widen only when the API is set.
- Generate and test `frontend/public`, not Vite's source shell.
- Publish static v3 data through no-cache pointers and immutable content-addressed
  assets.
- Deploy with `wrangler deploy` through `WebsiteService`: `workers/site` serves
  `frontend/public` as Static Assets and runs code only for `/preview/*`.
  Keep dashboard data and pages as static assets; do not route them through the Worker.
- The Vite bundle is rebuilt only when `frontend/` source or lockfile hash changes.
- Regular and hover chart-point radii stay zero; capacity-change markers stay
  visible. Horizontal guides render behind datasets with negative z-order.
- A successful build or upload log is not deployed-asset verification. Validate
  the hashed asset referenced by the generated manifest and hard-refresh.

## Worker

`workers/site/` is the site Worker: Static Assets plus code for `/preview/*`. Run
`make worker-check` (types, vitest, `tsc`, deploy dry-run) after changing it. Deploy it
only with explicit authorization, using the commands in `docs/operations.md`.

## Verification

Run the closest frontend unit test (`node --test frontend/test/<name>.test.mjs`) and build. Use generated-site smoke or browser
tests only when behavior or publication output changed. Production upload requires
explicit authorization.
