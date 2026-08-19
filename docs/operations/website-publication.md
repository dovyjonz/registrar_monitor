# Website publication model

The dashboard is a static Cloudflare Pages site generated from matching, non-empty
SQLite semester snapshots. `settings.toml` defines registrar sources, milestones,
and publication settings. Configured terms without valid stored courses are not
published.

Canonical terms are Fall, Spring, and Summer. Academic ordering follows registrar
years: Fall `YYYY`, Spring `YYYY`, then Summer `YYYY`.

## Routes and cache identity

- `/` redirects to the newest publishable semester.
- `/semesters/<semester>/` is the dashboard shell.
- `/courses/<semester>/<course>/` opens that course in the shared shell.
- `/data/<semester>/manifest.json` is the stable, revalidating semester pointer.
- Versioned manifests, blobs, preview JSON, preview images, and hashed assets are
  immutable and use long-lived caching.

Only generated legacy `.html` routes receive exact redirects. Removed courses keep
their last clean route and modal state. Archived semester/course URLs, semester
URLs, and canonical URLs remain clean. Live course browser/share/Open Graph URLs
carry the current preview-state token as `?v=<token>`; closing a course restores the
clean semester route.

Mutable HTML and stable semester pointers revalidate. Content-addressed artifacts
remain immutable. Generated-output validation rejects private files, root JSON
payloads, and obsolete non-index HTML. Every public page is `noindex, nofollow`;
`robots.txt` permits crawlers to fetch that directive.

## Enrollment semantics

Availability uses active sections. For each section type, sum
`max(capacity - enrollment, 0)`; a multi-type course has the minimum type total as
its registration places. Tied types are all limiting. Removed sections/courses do
not contribute, and over-capacity sections contribute zero open places.

When a multi-type course has no registration places because a required type is
full, `required-type-full` outranks ordinary Open/Near/Full. Compact copy can say
`LAB FULL`; roomier copy names the limiting type. Single-type courses keep `FULL`.

Archived publication selects the last real observation inside the configured
milestone window. Enrollment, capacity, availability, status, chart endpoint, and
preview state all derive from that observation. Later polls are not appended as
terminal data.

## Preview identity and Worker

Preview tokens encode the first 48 bits of the preview-visible SHA-256 identity as
eight unpadded URL-safe Base64 characters. A course identity includes only its
referenced observations/events, so unrelated changes and no-op polls do not churn
its URL. Semester identity includes the displayed observation time.

`workers/preview-images` is an isolated Worker. It validates state-addressed paths,
fetches exact preview JSON from the fixed Pages origin, verifies identity, renders
with the Browser binding, and caches only successful PNGs. It never reads SQLite,
contacts the registrar, or deploys Pages assets. Invalid state is a non-cacheable
404; fetch/render failure is a non-cacheable 5xx and cannot block static
publication or substitute stale content.

## Verification

Use `make site-smoke` for generated-output integrity, `make test-browser` for the
generated-site browser contract, `make worker-check` for preview rendering, and
`make release-candidate` for the integrated gate. Production deployment and Worker
deployment require separate operator authorization.
