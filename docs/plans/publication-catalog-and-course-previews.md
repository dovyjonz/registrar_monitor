---
status: implemented
updated: 2026-08-12
scope: website catalog, clean routes, metadata, course modal, and social previews
---

# Configuration-driven publication catalog and course previews

## Summary

Replace the hard-coded website semester catalog and redirect-only course share
pages with configuration-driven clean routes, richer metadata, and
state-addressed social preview images.

Keep the current dashboard and its course modal as the product UI. Do not build
a second course-summary interface. A clean course route loads the existing
dashboard shell, opens the existing modal for the requested course, and adds
course-specific metadata for link previews. Revise the modal only where the new
availability and historical states need to appear.

Cloudflare Pages continues to publish the static website, metadata, generated
JSON, and preview-state hashes. A separate Cloudflare Worker, routed only under
`/preview/*`, renders 1200x630 PNG previews on demand from an exact state already
published by Pages. It never reads the registrar or the live database.

This plan does **not** change polling, report timing, website publication
triggers, significant-change thresholds, or the existing five-minute website
cooldown. In particular, it does not introduce a new `:15`/`:45` publication
gate. The current scheduler remains authoritative for when publication occurs.

This is a personal project. Prefer direct code, existing project boundaries,
and a small focused test set. Do not add infrastructure or abstractions for
hypothetical scale.

## Intended architecture

```text
Registrar
   |
   v
existing monitor and SQLite snapshots
   |
   | existing publication triggers and cadence (unchanged)
   v
Cloudflare Pages
   |- dashboard and existing course modal
   |- clean semester and course HTML routes
   |- generated preview-state JSON
   `- deterministic state hashes
             |
             | /preview/course/fall-2026/ant-140/61bd09.png
             v
       Cloudflare Worker
       |- loads that exact published state from Pages
       |- renders a compact 1200x630 card
       `- returns and caches an immutable PNG
```

Pages remains the static-site deployment path. The Worker is only an image
renderer; it does not replace Pages and does not revive the retired Worker
asset-deployment path.

## Product outcome

After this work:

- `settings.toml` is the source of truth for semester sources, priorities, and
  deadlines.
- A semester appears only when its configuration and stored snapshot make it
  publishable.
- The latest publishable semester is selected automatically using the
  Fall-Spring-Summer academic-year sequence.
- `/` has evergreen metadata and keeps its convenient redirect to the latest
  publishable semester.
- Semester and course pages have clean directory-index URLs.
- Opening a course URL displays the existing dashboard course modal rather than
  a duplicate summary page.
- Link previews use immutable, state-addressed Worker image URLs.
- Removed courses retain their final published course route and modal state.
- All pages remain absent from search results but fetchable by link-preview
  crawlers.
- Narrow generated redirects cover only legacy URLs whose clean targets exist.

Root metadata remains:

```text
Title:
Enrollment Monitor

Description:
See historical and frequently updated undergraduate course data.
```

## Verified current project context

The following was checked against the repository on 2026-08-12 and should be
treated as the starting point, not work to reproduce.

### Storage and generated data

- SQLite is the source of truth, with one database per semester.
- Checkpointed history already records course and section identities, changes,
  removals, and reappearances.
- The website already uses a content-addressed v3 read model with a stable
  semester pointer, versioned manifest, summary blob, and lazy department
  history blobs.
- JSON remains generated website output. It is not another persistence layer.
- Per-semester checksums already skip unnecessary regeneration.

Do not change the database schema or ingestion contract for this project. The
current pipeline stores positive numeric capacity data and does not preserve a
useful distinction between missing capacity and a zero-capacity source row.
Consequently this plan has no `Availability unknown` state and no special
`0-capacity section` state.

### Existing priority implementation

Priority work is already substantially implemented:

- `settings.toml` contains per-semester priority and deadline configuration.
- `src/registrarmonitor/website/config.py::get_milestones` reads that
  configuration, orders priority groups, applies the existing palettes,
  normalizes registrar-local timestamps, and omits hidden labels.
- The frontend already renders priority progress, current/next milestone
  summaries, mobile labels, and chart annotations.
- Historical comparison code already aligns semester milestones.

Do not create another priority schema, parser, ordering table, palette, or
timeline renderer. If metadata or previews need a compact state such as
`PRIORITY 2`, cumulative eligibility, or the next milestone, derive it in one
small pure helper from the normalized milestone data already published to the
frontend.

### Existing course and chart implementation

The dashboard modal already provides the useful course view:

- combined course title and codes;
- grouped current sections with enrollment and capacity;
- instructors;
- course events;
- average and individual-section graphs;
- historical comparison;
- bookmarking and sharing;
- responsive mobile presentation.

Keep that modal as the single course-details renderer. Add the new availability
line and removed-course treatment there rather than introducing a second
course-summary template.

Recent chart work is also already implemented and must not be reimplemented:

- exactly 100% enrollment remains a visible graph point;
- over-capacity data remains visible and neutral;
- capacity uses observation-time step semantics;
- capacity changes do not move unchanged enrollment;
- removed sections stop contributing after removal;
- activity intervals derive from add/remove history;
- current and historical series share mapping helpers;
- mobile inspect, desktop pin, zoom, pan, and external readout behavior have
  focused coverage.

Use the pure helpers in `assets/website/src/chartMapping.mjs` and
`assets/website/src/historicalComparison.mjs` as the current semantic owners.
Extend them only where a shared preview model needs data they do not expose.

### Existing availability behavior and the required extension

The grid currently treats a course as filled when every section of at least one
section type is full. This matches the useful product assumption that every
section type is required. Grid open/near-full presentation also uses average
fill. Preserve those filters and visual states unless a concrete bug requires a
small correction.

The chart readout currently derives course enrollment and capacity by taking
the minimum enrollment total and minimum capacity total independently across
section types. Those two minima can come from different types and therefore do
not define a coherent availability value.

Introduce one pure availability calculation over the current active sections:

```text
available seats for one section type
= sum(max(capacity - enrollment, 0)) for its current sections

registration places for a multi-type course
= minimum available-seat total across section types
```

Examples:

```text
1 registration place available · Limited by labs.
Lectures 2/2 open · Labs 1/4 open · Tutorials 3/3 open.

4 seats open · 26/30 enrolled · Lecture 1/1 open.
```

Rules:

- One section type uses `seats open`; multiple types use `registration places`.
- A section counts as open when `capacity - enrollment > 0`; the per-type
  breakdown reports open sections over current sections.
- Clamp only the remaining-seat contribution to zero. Preserve the actual
  enrollment/capacity text, including `32/30 enrolled`.
- Tied minimum types are all limiting types.
- Use the source section-type name after the frontend's existing whitespace and
  capitalization normalization.
- The calculation operates on the numeric data the current database already
  supplies. It adds no missing-data or zero-capacity branches.
- Removed sections are excluded from current availability.
- Removed courses are excluded from current course and semester totals.

Reuse this helper for modal copy, course metadata, preview-state JSON, and the
Worker card. For chart readouts that show a paired enrollment/capacity total,
choose one deterministic limiting type and keep its enrollment and capacity
together; do not combine independent minima.

## Semester catalog

### Academic-year ordering

The registrar sequence is Fall, then Spring, then Summer within one academic
year:

```text
Fall 2025
Spring 2026
Summer 2026
Fall 2026
Spring 2027
Summer 2027
```

Use a simple academic sort key:

```text
Fall YYYY   -> (YYYY,   0)
Spring YYYY -> (YYYY-1, 1)
Summer YYYY -> (YYYY-1, 2)
```

Use ascending order for historical sequences and descending order for the
semester navigation/latest-semester choice. Recognize only `Fall`, `Spring`,
and `Summer` followed by a four-digit year.

### Registrar sources

Move the registrar source beside each semester's existing priorities and
deadlines. Preserve the complete source URL so the term association is obvious.

| Semester | Registrar source |
|---|---|
| Fall 2026 | `https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=825` |
| Summer 2026 | `https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=824` |
| Spring 2026 | `https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=823` |
| Fall 2025 | `https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=822` |
| Summer 2025 | `https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=805` |

Representative configuration:

```toml
[semesters."Fall 2026"]
registrar_url = "https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=825"

[semesters."Summer 2026"]
registrar_url = "https://registrar.nu.edu.kz/registrar_downloads/json?method=printDocument&name=xls_school_schedule_by_term&termid=824"
```

Add the field to the existing semester tables; do not replace or duplicate
their priority/deadline entries.

### Publishability

A semester is publishable when:

1. its recognized canonical label is configured;
2. it has a registrar source URL;
3. a successfully stored snapshot identifies the same semester;
4. the current snapshot contains at least one course.

Configured semesters without valid data remain hidden and cannot become the
root redirect target. Reject a newly downloaded response whose embedded
semester label conflicts with the configured semester before it changes the
published catalog.

Deadlines remain optional for old archived data. Keep the project's existing
policy for deadlines on newly added semesters; do not add a second validation
path solely for previews.

Remove the obsolete `SEMESTER_MAP`, `ALL_SEMESTERS`, `LATEST_SEMESTER`, duplicate
manual semester lists, and the global registrar URL only after every existing
consumer reads the per-semester catalog.

## Clean routes and the existing modal

Generate directory-index routes:

```text
/
/semesters/fall-2026/
/courses/fall-2026/ant-140/
```

The root route remains evergreen HTML with the generic root metadata and image.
It performs the existing client-side navigation to the latest publishable
semester and includes a visible fallback link for users when automatic
navigation does not run.

The semester route renders the existing dashboard. The course route uses the
same dashboard shell and assets, supplies course-specific `<head>` metadata,
and identifies the initial course to open. After the normal manifest data is
ready, the frontend opens the existing modal. This keeps the clean course URL in
the address bar without maintaining another course-page UI.

Use one shared HTML shell/template so semester and course entrypoints do not
drift. Make asset and data URLs root-absolute because the shell is served at
multiple route depths.

For a current course, hydrate the modal from the existing summary and lazy
department blob. For a removed course, hydrate the same modal from its final
published preview state. Removed courses remain absent from the current grid.

The modal revision is intentionally small:

- add the shared availability sentence above the current section groups;
- keep every existing current-section row and chart;
- show a prominent `REMOVED` badge and `Removed from the registrar listing`;
- show the final available/changed timestamp for a removed course;
- retain the existing event list rather than adding another previous-section
  interface;
- keep the existing archive/current navigation actions;
- make the share button copy the live versioned course URL, or the clean
  unversioned URL after the course is archived.

Do not create a separate narrow course summary, another section renderer, or a
second chart implementation.

### Legacy redirects

Stop generating obsolete files:

```text
/fall2026.html
/courses/fall-2026/ant-140.html
```

Generate an exact Cloudflare Pages `_redirects` entry only when its clean target
exists:

```text
/fall2026.html /semesters/fall-2026/ 301
/courses/fall-2026/ant-140.html /courses/fall-2026/ant-140/ 301
```

Unknown semesters and unknown course slugs have no generated rule and return
404. The present output is below the Pages static redirect limit; add a simple
build-time count guard, not a generalized redirect service.

## Metadata and search exclusion

Every HTML entrypoint includes:

```html
<meta name="robots" content="noindex, nofollow">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="...">
<meta name="twitter:card" content="summary_large_image">
```

Also generate an `_headers` rule applying:

```text
X-Robots-Tag: noindex, nofollow
```

Allow retrieval in `robots.txt`; do not use a site-wide `Disallow` that prevents
crawlers from seeing `noindex`. Remove optional per-page indexing behavior.

Canonical URLs are always clean and unversioned. For live pages, `og:url` and
the share URL carry the preview-state version:

```text
/courses/fall-2026/ant-140/?v=61bd09
```

When a live semester or course entrypoint is opened without its current version,
use `history.replaceState` after loading the published state. This changes only
the displayed URL; it does not navigate, reload, or affect the canonical tag.
The share button then copies that same displayed versioned URL.

Archived pages use a clean, stable, unversioned canonical and `og:url`. The
root page uses evergreen unversioned metadata and a static generic image.

## Published preview state

Build a small pure preview model from the already generated website data. Keep
it separate from HTML templates, Worker code, database access, and scheduler
logic.

The model contains only fields visible in metadata, the course modal addition,
or the image:

- normalized semester label and course slug;
- title and combined course codes;
- current/removed/archived state;
- compact priority state derived from existing milestones;
- course availability and per-type totals;
- current sections and the graph payload needed for the card;
- course `last changed` timestamp;
- semester course/section/full/open-seat aggregates;
- published snapshot timestamp for semester `updated` text;
- display labels.

Serialize it deterministically and calculate a short cryptographic hash from
the serialized preview-visible state. Reuse the project's content-addressing
utilities if they already provide the needed canonical serialization.

Course hashes change only when preview-visible course state changes, including:

- enrollment or capacity;
- title or codes;
- section addition, removal, or return;
- course removal/return;
- availability copy;
- visible priority/registration state;
- relevant graph data.

A poll timestamp alone does not change a course hash. `Last changed` is the
latest snapshot at which that preview-visible state changed. Semester hashes
include their published snapshot timestamp because the semester description
shows `Updated`.

Publish each exact state as content-addressed JSON, for example:

```text
/data/previews/course/61bd09.json
/data/previews/semester/a270c4.json
```

The document repeats its hash, kind, semester, and slug so both publication
validation and the Worker can reject mismatches. It contains no secrets and no
unpublished registrar data.

Keep older content-addressed state JSON that is still referenced by a published
course URL or immutable image URL. This is generated static output, not a new
database or mutable compatibility model.

## Worker-generated preview images

### URL model

Use state in the path rather than a query string:

```text
/preview/semester/fall-2026/a270c4.png
/preview/course/fall-2026/ant-140/61bd09.png
```

The HTML's `og:image` is an absolute version of that URL. `/previews/root.png`
remains a static evergreen Pages asset.

Archived course and semester pages retain their final state-addressed image URL.
It is already immutable, so archiving requires no image rewrite.

### Worker boundary

Create a small separate Worker project, for example
`workers/preview-images/`, and bind it only to `/preview/*` on the canonical
site domain.

For a request, the Worker:

1. validates the route shape, recognized semester, slug, and hash;
2. maps the request to a fixed Pages-origin preview-state URL;
3. fetches that JSON without following caller-controlled origins;
4. verifies kind, semester, slug, and hash against the request;
5. renders deterministic fixed-size HTML from that state;
6. takes a 1200x630 PNG screenshot through Cloudflare Browser Run;
7. returns the PNG and stores it in the Cache API under the full request URL.

Use the Browser Run browser binding and its screenshot Quick Action for this
stateless one-shot render. Do not manage a long-lived browser session unless a
real limitation appears during the rendering spike. Generate binding types from
the Worker configuration and keep the compatibility date current when the
Worker is implemented. Quick Actions currently require a compatibility date of
at least `2026-03-24` and remote bindings for `wrangler dev`; record those facts
in the Worker README rather than hiding them in a test script.

Successful responses use a long immutable cache policy:

```text
Cache-Control: public, max-age=31536000, immutable
```

Use `caches.default` and complete cache writes with the Worker execution
context. No KV, R2, D1, queue, Durable Object, or image index is needed
initially.

Invalid routes or missing/mismatched states return 404 and are not rendered.
Rendering or Browser Run failures return a concise non-cacheable 5xx response.
They do not block Pages publication and cannot cause an old PNG to be served at
a new state URL. Record enough structured context to identify the kind,
semester, slug, and hash without logging the full published payload.

The Worker must render from the exact published state JSON. It must never fetch
the live registrar, open SQLite, infer the latest state, or use an unversioned
course record.

### Card design and mobile compaction reuse

Use the website's existing design tokens and compacting behavior:

- dark navy background and card surfaces;
- monospaced typography;
- yellow primary accent and orange identity square;
- the existing neutral/open/full colors;
- normalized and compact section-type labels;
- the current compact priority/deadline labels;
- the chart's current point reduction before milestones;
- grouped section-type graphs when individual section labels no longer fit;
- clipped title and secondary text only at measured layout boundaries.

Extract or generate a small shared presentation payload so the dashboard and
Worker consume the same compact wording and chart series. Do not try to share
DOM code between the browser app and Worker. The shared contract should contain
the final labels, values, segments, and colors needed by both renderers.

Start with the existing responsive choices as the baseline: compact tooltip
copy, condensed milestone labels, section grouping, and limited pre-milestone
points. Render a few representative local states during the Worker spike—one,
several, and many sections; long title; multiple types; capacity change; and a
removed section—then choose the simplest threshold that remains readable.

Graph semantics remain those of the current chart mapping:

- 100% and over-capacity values are ordinary visible points;
- only absence ends a segment;
- a removed section ends at its last known observation;
- a return begins a new segment;
- enrollment is solid;
- capacity is a dashed step line;
- a capacity change may use one compact annotation.

## Semester and course descriptions

Reuse existing counts and milestone state where available. Keep copy compact.

Example live semester:

```text
Fall 2026 — Enrollment Monitor

404 courses · 884 sections · 127 full sections.
2,318 seats open · Updated 3 Aug, 15:45 Astana time.
```

Semester `seats open` is the raw sum of `max(capacity - enrollment, 0)` over
current sections. Do not sum course registration places.

Example archived semester:

```text
Spring 2026 — Enrollment Monitor

Registration closed · Historical data for 391 courses and 846 sections.
142 sections were full at the final update.
```

When no historical deadline exists, archived data may use the same
`Registration closed` label rather than inventing a priority timeline.

Priority copy is derived from the existing milestones, for example:

```text
PRIORITY 1
Y4+ and Y3 currently eligible
Next: Y2 · Today, 13:00

PRIORITY 3 · ALL
```

Do not add an overlapping `Open to all` state.

## Publication behavior

Do not modify the scheduler's timing or trigger behavior as part of this work.
Specifically preserve:

- the current polling schedule;
- scheduled report handling at `:15` and `:45` registrar-local time;
- significant-change website generation triggers;
- the current change threshold;
- the five-minute website update cooldown;
- existing forced-generation and forced-deployment behavior.

This project changes what a website generation publishes, not when the
scheduler asks it to publish.

Within each existing generation run:

1. load the newest stored state through current services;
2. build the publishable semester catalog;
3. generate changed semester and course entrypoints;
4. generate exact preview-state JSON and state hashes;
5. retain unchanged current routes and retained removed-course routes;
6. generate the route, redirect, and header files;
7. validate generated references;
8. use the existing direct Pages deployment path when deployment was requested.

Do not delete and recreate the full course-share tree. Write changed files and
retain unchanged and historical files. Once the clean modal entrypoint works
end to end, remove the obsolete redirect-only course template and old
current-courses-only share generator.

The Worker is deployed separately from Pages and only when explicitly
authorized. A Pages publication does not synchronously render images and does
not wait for the Worker.

## Implementation stages

Each stage should leave a working product and use the narrowest relevant tests.

### Stage 1: Canonical semester catalog

1. Add the supplied registrar URLs to the existing semester configuration.
2. Implement the Fall-Spring-Summer academic-year sort key.
3. Derive publishable semesters and latest semester from configuration plus
   stored snapshots.
4. Validate the embedded registrar semester label before accepting a download.
5. Move current consumers to the catalog.
6. Remove duplicate semester constants and the global term source.

Focused checks:

- academic ordering across the Fall boundary;
- configured future semester without data stays hidden;
- matching data becomes publishable and newest;
- mismatched embedded label is rejected.

### Stage 2: Shared preview and availability model

1. Add the pure availability helper over current active sections.
2. Derive compact priority state from the existing normalized milestones.
3. Produce deterministic semester/course preview-state JSON and hashes.
4. Reuse the current chart mapping for graph segments.
5. Correct paired chart-readout totals if the independent-minimum behavior is
   still reachable.

Focused checks:

- one-type seats;
- multi-type limiting registration places, including a tie;
- over-capacity contribution clamps to zero while text stays actual;
- removed sections do not count;
- state hash stays unchanged after a no-op poll;
- paired totals come from the same limiting type.

There are deliberately no missing-data, zero-capacity, or database migration
tests in this project.

### Stage 3: Clean dashboard and modal entrypoints

1. Generate clean semester routes with the existing dashboard shell.
2. Generate clean course routes with course metadata and initial-modal state.
3. Add availability and removed-course treatment to the existing modal.
4. Make sharing copy the live versioned course URL and the clean archived URL.
5. Add root metadata, unconditional noindex directives, `_headers`, and
   fetchable `robots.txt`.
6. Generate exact legacy redirects and remove obsolete `.html` output.
7. Stop deleting the complete course-share directory.

Focused checks:

- one live course URL opens the correct existing modal on a narrow viewport;
- one removed course URL opens its final modal state;
- canonical and versioned Open Graph URLs are correct;
- redirects all target generated routes;
- generated output contains no obsolete `.html` pages.

### Stage 4: Preview-image Worker

1. Scaffold the isolated `/preview/*` Worker and Browser Run binding.
2. Validate and fetch exact content-addressed Pages state.
3. Render the fixed card using existing compact labels and chart payloads.
4. Cache successful images by their immutable request URL.
5. Handle invalid state and rendering failure without affecting Pages.
6. Test locally against representative published fixtures.

Focused checks:

- the same state URL returns a cached PNG;
- a new hash is a different cache identity;
- a request cannot select an arbitrary upstream origin;
- full and over-capacity graph points remain visible;
- a missing state and a forced renderer error are non-cacheable failures.

Before production deployment, confirm Browser Run availability and limits for
the project's Cloudflare account and bind the route on the canonical domain.
That is an operational check, not a reason to complicate the initial design.

### Stage 5: Cleanup and documentation

1. Remove the redirect-only course template and superseded generators.
2. Remove manual semester collections after all consumers have moved.
3. Document the route model, academic ordering, publishability, availability,
   versioning, unchanged publication behavior, Worker boundary, non-indexing,
   and failure behavior.
4. Update the production topology only after the Worker is actually deployed.

## Focused verification

Avoid a large combinatorial matrix. The implementation is ready when these
observable behaviors pass:

- The order is Fall 2025, Spring 2026, Summer 2026, Fall 2026.
- A configured semester without a stored matching snapshot is hidden and does
  not become `/`'s target.
- A matching Fall 2026 snapshot becomes publishable automatically; a mismatched
  response does not alter the current catalog.
- Existing scheduler tests show no cadence, threshold, or cooldown change.
- Existing priority UI and annotations still work; new compact copy is derived
  from the same milestone payload.
- A course with 9 lecture, 1 lab, and 12 tutorial seats remaining reports
  `1 registration place available · Limited by labs.`
- A one-type course uses `seats open`.
- A `32/30` section remains neutral and visible and contributes zero remaining
  seats.
- Exactly 100% and over-capacity graph points remain visible in the dashboard
  and Worker preview.
- A removed section leaves current availability and ends its graph segment; a
  return starts a new segment using existing event identity.
- A clean course URL opens the current modal, including on a narrow viewport.
- A removed course URL opens the same modal with its final graph and `REMOVED`
  state while remaining absent from current totals.
- An unchanged course preserves its hash across a later generation.
- A changed course produces a new page/share version and immutable image path.
- The Worker renders only the exact state named by the URL and caches the PNG.
- A Worker rendering failure cannot block static publication or serve an old
  image at a new hash.
- Canonical URLs remain clean; live `og:url` values are versioned; archived
  values are stable.
- Every generated HTML entrypoint is `noindex, nofollow`, response headers match,
  and `robots.txt` permits retrieval.
- Every legacy redirect targets an existing clean route; unknown legacy shapes
  return 404.

Run the closest Python or frontend unit tests during each stage. Run the
existing fast project checks and one representative browser flow before handoff.
Use the full project check only for the final cross-cutting integration, not for
every small iteration.

## Explicit non-goals

- No change to website publishing cadence or scheduler behavior.
- No database schema change, database rewrite, or missing/zero-capacity model.
- No second course-summary page or duplicated event-history interface.
- No new priority configuration or replacement timeline renderer.
- No live registrar access from the Worker.
- No Worker-based website asset deployment.
- No PNG generation during Pages publication.
- No permanent PNG collection in each Pages deployment.
- No KV, R2, D1, queue, Durable Object, or generalized rendering service.
- No compatibility model for obsolete HTML pages; only narrow generated HTTP
  redirects to clean routes.
- No deployment, VM synchronization, or service restart without explicit
  operator authorization.

## Assumptions

- All section types present for a course are required; section pairing and
  approval restrictions are outside the availability estimate.
- Current stored enrollment and capacity values are sufficient for this
  project; missing and zero-capacity source distinctions remain out of scope.
- Over-capacity values are valid neutral data caused by capacity changes or
  approved registrations.
- One disappearance is enough for current history logic to mark removal, and a
  later reappearance is a return.
- Pages can publish the small content-addressed state documents needed by the
  Worker.
- The canonical site domain can route `/preview/*` to the dedicated Worker.
- Browser Run's screenshot Quick Action is sufficient for the fixed HTML card;
  verify that assumption with representative local renders before adding more
  browser-session machinery.

## Current Cloudflare references

These implementation choices were checked against Cloudflare's current primary
documentation on 2026-08-12:

- [Browser Run Quick Actions](https://developers.cloudflare.com/browser-run/quick-actions/)
  for one-shot screenshots, Worker bindings, compatibility date, and remote
  local development;
- [Wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/)
  for a separate Worker configuration as its deployment source of truth;
- [Workers Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)
  for `caches.default`, cache headers, and its per-data-center behavior.
