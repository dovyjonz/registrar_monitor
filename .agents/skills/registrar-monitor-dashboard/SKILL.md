---
name: registrar-monitor-dashboard
description: Build, debug, verify, or deploy the Registrar Monitor generated Vite dashboard, immutable static data, course routes, preview images, or Cloudflare Pages publication.
---

# Registrar Monitor dashboard

Read `docs/operations/website-publication.md` and the website section of
`docs/operations/tooling.md`.

## Rules

- Generate and test `assets/website/public`, not Vite's source shell.
- Publish static v3 data through no-cache pointers and immutable content-addressed
  assets.
- Deploy Pages by direct upload through `WebsiteService`. The preview-image Worker
  is separate.
- Regular and hover chart-point radii stay zero; capacity-change markers stay
  visible. Horizontal guides render behind datasets with negative z-order.
- A successful build or upload log is not deployed-asset verification. Validate
  the hashed asset referenced by the generated manifest and hard-refresh.

## Verification

Run the closest frontend unit test and build. Use generated-site smoke or browser
tests only when behavior or publication output changed. Production upload requires
explicit authorization.
