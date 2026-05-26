# Registrar Monitor Wiki Schema

This wiki is maintained as a persistent LLM-authored knowledge base for **UC Berkeley Summer 2026 Registrar enrollment monitoring, snapshot comparison, and reporting**.

## Layers

1. **raw/** - immutable source capture packets
2. **wiki/** - editable source pages and canonical knowledge pages
3. **meta/** - generated registry, backlinks, index, logs, and reports
4. **schema** - this file and .wiki/config.json

## Non-negotiable rules

- Never directly edit raw/**.
- Never hand-maintain generated metadata under meta/**.
- Every source must become a source page before it influences canonical pages.
- Update existing canonical pages before creating new ones.
- Use folder-qualified wikilinks such as [[concepts/example-topic]].
- Cite factual claims with source page ID links such as [[sources/SRC-YYYY-MM-DD-NNN|SRC-YYYY-MM-DD-NNN]].
- Query mode is read-only by default; file durable answers deliberately into wiki/analyses/.
- Use Tensions / caveats and Open questions whenever evidence is uncertain.

## Page Taxonomy

- wiki/sources/ = what one source says
- wiki/concepts/ = stable concepts tracked over time
- wiki/entities/ = people, orgs, products, papers, etc.
- wiki/syntheses/ = cross-source theses and unresolved tensions
- wiki/analyses/ = durable filed answers from queries

## Source-page standard

Every source page should answer these questions:
- What is this source?
- What are its main claims?
- What concrete details or data points matter?
- Which concepts and entities does it touch?
- How reliable or limited is it?
- Which canonical pages should be updated because of it?

Fill these sections whenever possible:
- Source at a glance
- Executive summary
- Main claims
- Important details and data points
- Entities and concepts mentioned
- Reliability / caveats
- Integration targets
- Open questions

## Workflows

### Capture
1. Use the capture tool to preserve the source packet.
2. Read the extracted content and source page.
3. Improve the source page first.
4. Only then update impacted canonical pages.
5. Log integration when done.

### Query
1. Search the wiki first.
2. Read the most relevant pages.
3. Answer using source page citations.
4. Only create analysis pages if asked or if the result is explicitly worth filing.

### Audit
1. Run deterministic lint for structural issues.
2. Then reason about semantic gaps, contradictions, stale theses, and missing pages.
3. Report tensions before resolving them.
