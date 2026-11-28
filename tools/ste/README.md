# STE check

```bash
python3 tools/ste/ste.py                     # every tracked AGENTS.md and SKILL.md
python3 tools/ste/ste.py simulation/AGENTS.md
bazel test //tools/ste:ste_test
```

This check enforces the mechanical rules of the
[simple-technical-english](../../docs/skills/simple-technical-english/SKILL.md)
skill in agent docs. CI runs it in the `Lint` job. It fails on:

- a semicolon in prose
- a sentence of more than 25 words
- a phrasal verb, a marketing word, a stacked hedge or a noun form of an
  action from the lists in `BANNED`.

The other rules of the skill need judgment. A reviewer checks those.

## What it reads

The check reads `AGENTS.md` and `SKILL.md` files, because agents follow
them step by step. Each paragraph, list item and table cell is a separate
unit. The check skips frontmatter, headings, code fences, code spans, URLs
and text in double quotes. A code span counts as one word. A link counts
as the words of its text.

## Exceptions

There is no baseline file
([ADR-017](../../docs/architecture/017-tests-gate-merges.md)). Rewrite the
text. If a paragraph must break a rule, for example a quote from a
datasheet, put this comment on the line before it:

```markdown
<!-- ste-ignore: quotes the BMS datasheet word for word -->
```

The comment needs a reason, and it skips only the next paragraph.

## Add a word

Add a word to `BANNED` in `ste.py` with a case in `ste_test.py`. Run the
check on every agent doc in the same PR, and fix what it finds.
