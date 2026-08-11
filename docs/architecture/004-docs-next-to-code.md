# ADR-004: Docs live next to code, published as one site

- **Status:** Accepted
- **Date:** 2026-08 (backfilled — the decision predates this record)

## Context

Student-team knowledge usually lives in a wiki or a shared drive, where it
is invisible from the code, unreviewed, and stale within a season. Docs
that aren't part of the change that made them wrong never get fixed.

## Decision

Documentation lives **next to the code it describes**: every directory
with non-trivial code has a `README.md`, and CI publishes the whole tree
as a MkDocs site on every merge to `main` (the `simple` plugin turns any
`.md` in the tree into a page; `README.md` renders as the directory
index). Source files can embed doc pages via `/** md` / `// md` comment
blocks for docs that would go stale if separated from the code. Repo-wide
pages with no single home directory (like [build-system.md](../build-system.md)
and these ADRs) live in `docs/`.

Docs are a required output of every change — behavior changes update the
adjacent README in the same PR — and `mkdocs build --strict` runs in
presubmit, so a broken link between pages fails CI.

## Alternatives considered

- **GitHub wiki.** Separate history, no review, invisible from the code it
  describes — the canonical place docs go to die.
- **Shared drive / Notion.** Same failure plus a tooling boundary: nothing
  connects a code change to the doc it invalidates.
- **A central `docs/` tree for everything.** Reviewable, but far from the
  code; directory docs drift because editing them isn't on the path of
  editing the code.

## Consequences

- Docs are reviewed in the same PR as the change, and link rot fails
  presubmit instead of accumulating.
- The site's structure mirrors the repo, so "where is this documented" and
  "where does this live" have the same answer.
- Two linking rules to remember (enforced by how the site builds): between
  markdown files use relative links; to code/config files use full GitHub
  URLs, because non-markdown files don't exist on the published site.
