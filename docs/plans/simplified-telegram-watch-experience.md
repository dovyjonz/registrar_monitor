# Simplified Telegram watch experience

## Problem Statement

Registrar Monitor's private Telegram bot exposes the same capabilities through too
many competing entry points. A user currently sees separate concepts for search,
catalog browsing, subscriptions, status, settings, help, and website import. The
interface is technically capable, but it does not make the shortest path obvious:
add a watch, then return later to inspect or amend that watch.

The `/start` message describes the product but does not teach the quickest input
format or establish a narrow navigation hierarchy. The public Telegram command
menu exposes eight commands, several of which duplicate actions already available
inside course and watch screens. The watch list and section picker also use labels
and glyphs that add visual weight or render inconsistently across Telegram clients.

The generated dashboard presents its Telegram bookmark export as another large
filter button. Its long label competes with course filters even though copying
starred courses is an action on a selection, not a filter.

The underlying performance and watch-management capabilities are already present.
Different private chats are processed concurrently with per-chat ordering, slow
updates are instrumented at three seconds, blocking catalog and bot-store work is
kept off the event loop, exact `/watch` input adds courses or sections quickly, and
existing watches can be amended. The remaining problem is the user-facing
information architecture.

## Solution

Make the private bot feel like it has two primary jobs: **Add a watch** and **My
watches**. Teach the typed shortcut directly in `/start`, use `watch` consistently
in user-facing copy, and move browsing, current enrollment, import, settings, and
destructive actions into the context where each is needed.

The normal `/start` response will contain a short explanation, the active semester,
the exact course-code example, and only two buttons: **Add a watch** and **My
watches**. Add a watch will accept an exact course or section code immediately,
support title search, and offer catalog browsing as a secondary action. My watches
will show compact target labels that open watch details, where users can inspect
current enrollment, change scope, stop watching, or reach data settings.

The visible Telegram command menu will contain only `/start`, `/watch`, `/watches`,
and `/help`. Website import remains a supported hidden command because copied
dashboard text depends on it. Capabilities currently exposed as standalone
catalog, subscriptions, status, and settings commands will instead be reached
through the two primary paths. Obsolete public command handlers will be removed,
not retained as aliases.

On the generated dashboard, the starred-course Telegram action will move out of
the filter group and become a compact contextual utility labeled **Copy for bot**
or **Copy N for bot**. It will retain a descriptive accessible name and a safe
interactive target. Copying will continue to produce the validated portable
`/import` message and will explain that the user should paste it into Registrar
Monitor on Telegram.

## User Stories

1. As a new bot user, I want `/start` to explain what the bot sends, so that I understand its purpose immediately.
2. As a new bot user, I want `/start` to show an exact course-code example, so that I can add a watch without exploring menus.
3. As a new bot user, I want to know which semester is active, so that I understand the scope of the watch I am creating.
4. As a bot user, I want only Add a watch and My watches on the home screen, so that the next action is obvious.
5. As a bot user, I want to send an exact course code, so that I can watch a whole course in one quick interaction.
6. As a bot user, I want to send an exact course and section code, so that I can watch one section in one quick interaction.
7. As a bot user who does not know an exact code, I want to search by title, so that I can still find the course.
8. As a bot user who prefers exploration, I want Browse all courses inside Add a watch, so that catalog browsing remains discoverable without occupying the home screen.
9. As a bot user, I want search results to lead to one course screen, so that whole-course and section choices share one clear context.
10. As a bot user, I want a course screen to distinguish Watch whole course from Choose sections, so that I understand the scope I am selecting.
11. As a bot user, I want My watches to group watches by semester, so that each target's scope is clear.
12. As a bot user, I want watch-list buttons to contain only course and section labels, so that the list is compact and scannable.
13. As a bot user, I want tapping a watch to open its details instead of removing it, so that ordinary navigation is safe.
14. As a bot user, I want a watch detail screen to show the latest stored enrollment and observation time, so that I can check current status in context.
15. As a whole-course watcher, I want to switch to selected sections, so that I can narrow an existing watch without deleting and recreating it.
16. As a section watcher, I want to add or remove sections, so that I can amend the watch as my schedule changes.
17. As a section watcher, I want a compact review of proposed changes before they are applied, so that I can catch accidental removals.
18. As a bot user, I want destructive removal to require confirmation, so that an accidental tap does not delete a watch.
19. As a bot user, I want Cancel or Back actions during editing, so that I can leave without changing the bot store.
20. As a bot user, I want Add a watch available from an empty watch list, so that the empty state directly helps me continue.
21. As a bot user, I want Add a watch available after reviewing existing watches, so that I do not need to return home first.
22. As a privacy-conscious user, I want Data and settings reachable from My watches, so that destructive account controls remain discoverable without dominating normal navigation.
23. As a bot user, I want one user-facing term, watch, used throughout the interface, so that I do not have to distinguish watches from subscriptions.
24. As a bot user, I want a short public command menu, so that Telegram's command suggestions do not feel overwhelming.
25. As a bot user, I want Help to explain the same two-path hierarchy, so that documentation reinforces the interface.
26. As a bot user on macOS or another Telegram client, I want controls to use portable text instead of emoji or checkbox glyphs, so that labels render consistently.
27. As a bot user, I want user-facing copy to avoid en dashes and em dashes, so that typography remains consistent across clients.
28. As a dashboard user with starred courses, I want a compact Copy for bot action, so that Telegram export does not compete with filters.
29. As a dashboard user with several starred courses, I want the copy action to show the selection count, so that I know what will be copied.
30. As a dashboard user with no starred courses, I do not want to see an inactive Telegram export action, so that the controls remain uncluttered.
31. As a dashboard user, I want copying to preserve the current semester and starred course codes, so that the bot can validate the intended watches.
32. As a dashboard user, I want confirmation after copying, so that I know to paste the message into Registrar Monitor on Telegram.
33. As a keyboard or assistive-technology user, I want the compact copy action to have a clear accessible name and usable target, so that visual compactness does not reduce operability.
34. As a user opening a course deep link, I want the existing confirmation behavior preserved, so that a link never creates a watch silently.
35. As a user receiving a personal digest, I want its management action to open My watches, so that notification follow-up uses the same vocabulary and hierarchy.
36. As a user, I want navigation changes to leave my stored watches intact, so that simplifying the interface does not alter subscription target semantics.
37. As an operator, I want the bot's latency, concurrency, and delivery behavior to remain unchanged, so that a UX simplification does not regress runtime reliability.

## Implementation Decisions

- `watch` is the only user-facing noun for a saved course or section. Internal domain language remains `subscription target`, and storage schema names do not change.
- The normal `/start` response uses this content hierarchy: product name, one-sentence explanation, exact typed example, active semester, Add a watch, and My watches.
- The start explanation states that the bot sends a private message when enrollment changes for a watched course or section. It does not expose report-cycle or notification-batch terminology.
- Deep-link `/start` payloads keep their validated course-or-section confirmation flow and do not show the normal home response first.
- The home keyboard contains exactly two actions: Add a watch and My watches.
- Add a watch prompts for `CSCI 115`, `CSCI 115 / 1L`, or a course title. Exact valid targets retain immediate creation. Non-exact text uses the existing search behavior.
- Browse all courses is a secondary action inside Add a watch. Search and catalog browsing are no longer separate top-level concepts.
- A course screen provides Watch whole course, Choose sections, and contextual navigation. Current stored enrollment remains part of that screen.
- My watches groups subscription targets by semester and labels each button with only the course code and optional section code. The word `Edit` is omitted because opening the target establishes the edit context.
- An empty My watches screen includes a direct Add a watch action.
- Opening a watch displays its current scope and latest stored enrollment. It offers the relevant scope change, Stop watching, and Back to my watches.
- A whole-course watch can become a set of section watches, and a section set can be amended without an unsubscribe-and-recreate workflow.
- Section controls use explicit portable text. Selected buttons use labels such as `1L selected`; unselected buttons use the section code. Emoji, ballot-box characters, cross marks, and decorative symbols are not used to communicate state.
- Section edits continue to use a compact receipt before application. The picker action is Review changes; the receipt action is Save changes. Back or Cancel leaves the bot store unchanged.
- Stop watching and bot-data deletion remain destructive operations that require explicit confirmation.
- Data and settings is a secondary action at the bottom of My watches. Clear all watches and Delete my bot data live there rather than in the primary command hierarchy.
- The public command menu contains exactly `/start`, `/watch`, `/watches`, and `/help`.
- `/watches` replaces `/subscriptions`. The obsolete `/subscriptions`, `/catalog`, `/status`, and `/settings` command handlers are removed rather than kept as aliases.
- `/import` remains registered but is omitted from the visible command menu. It is a transport contract for dashboard-generated text, not a concept users must discover in Telegram.
- Help documents the two primary paths, exact typed formats, website paste behavior, and how to reach Data and settings. It does not present a flat list of every internal handler.
- Personal-digest management links open My watches and use the same user-facing terminology.
- The generated dashboard treats Telegram export as an action on starred courses, not as a course filter. The action is rendered outside the filter-button group and only when the current semester has starred courses.
- The dashboard label is `Copy for bot` for one course and `Copy N for bot` for multiple courses. Its accessible name is `Copy starred courses for the Telegram bot` with the selection count included when useful.
- The dashboard action is visually compact on desktop while preserving keyboard focus visibility and a safe interactive target. Mobile layout must not add it as another equal-width filter cell.
- The copy payload remains the existing portable multi-line `/import` command containing the current semester and starred course codes.
- Successful copy feedback says `Copied. Paste this into Registrar Monitor on Telegram.` Failure feedback remains actionable.
- User-facing bot copy and the dashboard Telegram action use no emoji, en dashes, or em dashes. Plain punctuation and words communicate meaning.
- No bot-store or enrollment-store schema changes are required.
- Enrollment snapshots remain the source for search and displayed status. The bot does not poll the registrar.
- Notification batches, deliverable-batch rules, personal-digest matching, retry behavior, update concurrency, per-chat ordering, catalog caching, off-event-loop work, and latency instrumentation are unchanged.
- User-facing operational documentation is updated to describe `/watches` and the new hierarchy. Obsolete command documentation is removed.

## Testing Decisions

- Tests assert observable messages, buttons, navigation, copied text, and persisted subscription targets. They do not assert private helper structure or duplicate every handler branch.
- The primary Telegram seam drives public commands and callbacks through the private interaction layer using fake Telegram updates, a real temporary bot store, and a representative enrollment catalog.
- Telegram journey coverage includes normal `/start`, Add a watch, exact whole-course and section input, title search, Browse all courses, My watches, opening an existing target, changing scope, reviewing and saving sections, cancellation, destructive confirmation, Data and settings, help, deep-link confirmation, and pasted dashboard import.
- Telegram tests assert that the public command menu contains exactly four entries and that obsolete public command handlers are not registered.
- Telegram tests assert that visible messages and buttons use `watch` terminology and contain no emoji, ballot-box glyphs, en dashes, or em dashes.
- Existing private-interaction tests with fake Telegram boundaries and real temporary SQLite are the prior art for the bot seam.
- The primary dashboard seam generates the site and exercises the starred-course action in a rendered browser page.
- Dashboard browser coverage verifies contextual visibility, placement outside the filter group, singular and counted labels, clipboard content, success feedback, keyboard focus, and responsive behavior.
- Focused frontend unit coverage verifies portable import payload construction and label/count state without replacing the rendered browser acceptance test.
- Existing generated-template tests, frontend unit tests, generated-site smoke checks, and Playwright browser tests are the prior art for the dashboard seam.
- The focused Telegram tests and frontend tests run during iteration. The full `make check` gate verifies Python and frontend integration. Generated-site smoke and the relevant browser test verify the rendered dashboard before completion.
- No test contacts Telegram, polls the registrar, mutates production databases, or publishes the generated dashboard.

## Out of Scope

- Changes to subscription target semantics, notification matching, personal digest contents, delivery retries, or channel reporting.
- Changes to bot-store or enrollment-store schemas.
- Additional update concurrency or latency architecture beyond the implementation already present.
- A new Telegram Mini App, custom reply keyboard, web account, authentication flow, or bot username configuration.
- Directly sending dashboard bookmarks to Telegram without an explicit clipboard-and-paste action.
- Carrying watches automatically between semesters.
- Redesigning unrelated dashboard search, course filters, elective filters, sorting, course cards, charts, or preview routes.
- Changing Telegram's native button styling or relying on client-specific visual state.
- Production bot restart, service activation, VM synchronization, Cloudflare Pages upload, or any other production mutation.

## Further Notes

- The current runtime already processes different private chats concurrently with a bound of eight while preserving per-chat order. It logs each update's processing latency and warns at three seconds. This specification must not duplicate or replace that architecture.
- Exact `/watch COURSE` and `/watch COURSE / SECTION` behavior and existing watch amendment are already implemented. The work described here makes those capabilities easier to discover and presents them through one coherent hierarchy.
- The generated dashboard and Telegram bot remain loosely coupled through a validated portable import message. No bot username or visitor account is required.
- Production remains unchanged until separately and explicitly authorized.
