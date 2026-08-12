# Preview image Worker

This isolated Worker serves only immutable `/preview/*` PNG routes. It validates
the route, fetches the exact content-addressed preview JSON from the fixed Pages
origin, verifies the document identity, renders a 1200×630 card with Browser Run,
and caches successful PNGs by the full request URL.

Browser Run Quick Actions require a compatibility date of at least `2026-03-24`.
They are unavailable in local-only mode, so the browser binding uses
`"remote": true`; live development therefore consumes Cloudflare Browser Run
quota. `npm test` does not call the remote renderer. Run `npm run check` for
types, unit tests, and a Wrangler dry-run bundle.

The Worker has no registrar, SQLite, KV, R2, D1, queue, or Durable Object access.
`PAGES_ORIGIN` is fixed in `wrangler.jsonc`; request paths cannot select another
upstream. Missing or mismatched states return non-cacheable 404 responses, while
fetch/render failures return non-cacheable 502 responses.

The production Worker is published at
`registrar-monitor-preview-images.spooktaken.workers.dev`; `settings.toml`
uses that separate origin for preview metadata. Keep Pages as the website and
preview-state deployment path.
