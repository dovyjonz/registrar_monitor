# Website publication model

The dashboard is a static Cloudflare Pages site generated from matching,
non-empty SQLite semester snapshots. `settings.toml` defines each semester's
registrar URL and milestones; configured semesters without matching stored data
are not published.

Academic ordering follows registrar years rather than calendar years: Fall
`YYYY` sorts as `(YYYY, 0)`, Spring `YYYY` as `(YYYY-1, 1)`, and Summer `YYYY`
as `(YYYY-1, 2)`. Only canonical `Fall`, `Spring`, and `Summer` labels are
recognized. A configured term is publishable only when it has a registrar URL,
a successfully stored snapshot with the same embedded semester label, and at
least one current course. A mismatched download is rejected before storage.

## Routes and data

- `/` is evergreen redirect HTML for the newest publishable semester.
- `/semesters/<semester>/` is the shared dashboard shell.
- `/courses/<semester>/<course>/` uses that shell and opens the existing course
  modal after the v3 manifest loads.
- `/data/<semester>/manifest.json` is the mutable semester pointer. Manifests,
  blobs, and `/data/previews/<kind>/<hash>.json` are immutable.
- `_redirects` contains exact redirects only for generated legacy `.html` URLs.

Removed courses retain their last clean route and final published modal state.
All but the newest publishable semester are archived; archived semester and
course share URLs stay clean and unversioned. Live share and Open Graph URLs use
the preview-state hash as `?v=<hash>`. Canonical URLs are always clean.

Every page and response is marked `noindex, nofollow`, while `robots.txt` allows
crawlers to fetch the metadata. Generated output validation rejects private
files, root JSON payloads, and obsolete non-index HTML files.

Course availability is calculated from current active sections. For each
section type it sums `max(capacity - enrollment, 0)`; a multi-type course has
the minimum of those totals as its available registration places. Tied types
are all named as limiting. A single-type course says `seats open`. Removed
sections and courses do not contribute to current totals, while over-capacity
enrollment remains visible and contributes zero open seats.

Preview-state hashes cover only preview-visible data. Course timestamps are
compacted to the observations and events referenced by that course, so a later
no-op poll or an unrelated course change does not create a new course URL.
Semester hashes include their published observation time because the semester
description displays it. Existing immutable state files are retained.

## Preview images

`assets/website/previews/root.png` is the evergreen 1200×630 root image. Its SVG
source and the dynamic renderer use the dashboard's JetBrains Mono font stack
and HSL color tokens.

`workers/preview-images` is an isolated Worker project for state-addressed
semester and course PNGs at
`registrar-monitor-preview-images.spooktaken.workers.dev`. It accepts only
validated `/preview/.../<hash>.png` paths, fetches the exact preview JSON from a
fixed Pages origin, verifies the identity, renders through the Browser binding,
and caches successful PNGs as immutable. It does not read SQLite or contact the
registrar.

The card uses the dashboard's shared renderer-neutral chart presentation and
defaults to milestone-phased coordinates. Registration phases receive equal
visual weight while long edge periods are compressed; this is more legible for
registration history than raw time or snapshot ordinals. Enrollment is a solid
observation-step series, capacity is a dashed observation-step series, and
section removal/return creates separate segments. The visible `Priority 1`,
`Priority 2`, `Priority 3`, and deadline guides are equally spaced; hidden
year-level milestones do not consume chart width, but remain visible as thin,
unlabeled intermediate guides. Section-type labels use the dashboard's full
names, such as `Lecture` and `Lab`.

The Worker is not part of the Pages asset deployment path. Deploy and bind it
separately only after operator authorization; see its local README for checks
and remote-development requirements.

Pages publication timing is unchanged: the scheduler, significance thresholds,
and existing five-minute website cooldown remain authoritative. The Worker
only reads an exact immutable state after Pages has published it. Invalid or
mismatched state returns a non-cacheable 404; state-fetch or Browser Run failure
returns a non-cacheable 5xx and cannot block static publication or substitute an
older image. Only successful PNGs are cached for one year by their full
state-addressed URL. Every HTML route and response remains `noindex, nofollow`,
while `robots.txt` permits crawlers to retrieve that directive.

## Verification

Use `make site-smoke` to generate and crawl an isolated deployable site. The
normal `make check` also runs Python, frontend, and browser checks. The Worker
has its own commands under `workers/preview-images`:

```bash
npm test
npx tsc --noEmit
npx wrangler deploy --dry-run
```
