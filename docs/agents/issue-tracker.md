# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `dovyjonz/registrar_monitor`. Use `gh`
from this checkout so it resolves the repository from `origin`.

## Operations

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Pull requests are not a triage request surface. If a bare issue number may refer
to a PR, try `gh pr view <number>` before `gh issue view <number>`.

When a skill says to publish work, create a GitHub issue. When it says to fetch a
ticket, read the issue and its comments.

## Wayfinding

- The map is one issue labelled `wayfinder:map`.
- Children are GitHub sub-issues where available; otherwise link them from a task
  list and put `Part of #<map>` in each child.
- Use labels `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or
  `wayfinder:task`.
- Express blockers with GitHub issue dependencies. If unavailable, put
  `Blocked by: #<number>` at the top of the child.
- The frontier is the first open, unassigned child with no open blocker.
- Claim with `gh issue edit <number> --add-assignee @me`.
- Resolve by commenting with the answer, closing the child, and linking the
  decision from the map.

External writes still require authorization appropriate to the current request.
