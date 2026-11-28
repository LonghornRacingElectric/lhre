"""Checks the mechanical Simplified Technical English (STE) rules in agent docs.

The rules come from docs/skills/simple-technical-english/SKILL.md. This
check enforces only the rules that need no judgment: no semicolons, no
sentence longer than MAX_WORDS, and no word from the banned lists.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

DOC_NAMES = ("AGENTS.md", "SKILL.md")
MAX_WORDS = 25

BANNED = {
    "phrasal verb": r"(set|sets|setting) up|(spin|spins|spinning|spun) up|(kick|kicks|kicked|kicking) off"
    r"|(figure|figures|figured|figuring) out|(find|finds|finding|found) out|(carry|carries|carried|carrying) out"
    r"|(come|comes|came|coming) up with|(look|looks|looked|looking) into|(dive|dives|dived|diving) into"
    r"|(reach|reaches|reached|reaching) out|(end|ends|ended|ending) up|(point|points|pointed|pointing) out",
    "marketing word": r"robust|seamless|seamlessly|powerful|cutting-edge|blazing|effortless|effortlessly"
    r"|world-class|best-in-class|state-of-the-art|game-changing|leverages?|leveraged|leveraging",
    "stacked hedge": r"(may|might|could|can) (possibly|potentially|perhaps)|it is important to note",
    "noun form of an action": r"make use of|provides? assistance"
    r"|(perform|performs|performed|performing|conduct|conducts|conducted|conducting) (a|an|the) \w+(tion|sion|ment|sis|ance|ence)",
}
_BANNED = {kind: re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE) for kind, pattern in BANNED.items()}

_FENCE = re.compile(r"^\s*(```|~~~)")
_ITEM = re.compile(r"^\s*([-*+]|\d+\.)\s+")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_IGNORE = re.compile(r"<!--\s*ste-ignore:\s*(\S.*?)\s*-->")
_STRIP = (
    (re.compile(r"<!--.*?-->"), " "),
    (re.compile(r"`[^`]*`"), " Code "),
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"https?://\S+"), " link "),
    (re.compile(r"\"[^\"]*\"|“[^”]*”"), " quote "),
    (re.compile(r"[*_]{1,3}"), ""),
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")
_WORD = re.compile(r"[A-Za-z0-9]")


def doc_files(root):
    out = subprocess.run(
        ["git", "ls-files", "-z", *(f"*{n}" for n in DOC_NAMES)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(Path(p) for p in out.split("\0") if p and Path(p).name in DOC_NAMES)


def units(text):
    """Yields (first line number, prose) for each paragraph, list item and table cell."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0] == "---" and "---" in lines[1:]:
        start = lines.index("---", 1) + 1
    fenced = False
    ignored = False
    current, first = [], 0
    for n, line in enumerate(lines[start:], start + 1):
        if _FENCE.match(line):
            fenced = not fenced
            line = ""
        elif fenced:
            continue
        boundary = not line.strip() or line.lstrip().startswith(("#", "|")) or _ITEM.match(line)
        if boundary and current:
            if not ignored:
                yield first, " ".join(current)
            current, ignored = [], False
        if _IGNORE.search(line):
            ignored = True
            continue
        if not line.strip() or line.lstrip().startswith("#") or _TABLE_RULE.match(line):
            continue
        if line.lstrip().startswith("|"):
            for cell in line.strip().strip("|").split("|"):
                if cell.strip() and not ignored:
                    yield n, cell
            ignored = False
            continue
        if not current:
            first = n
        current.append(_ITEM.sub("", line, count=1).strip())
    if current and not ignored:
        yield first, " ".join(current)


def clean(prose):
    for pattern, repl in _STRIP:
        prose = pattern.sub(repl, prose)
    return " ".join(prose.split())


def check_text(text):
    """Returns [(line, message)]."""
    errors = []
    for line, prose in units(text):
        prose = clean(prose)
        if ";" in prose:
            errors.append((line, "semicolon: write two sentences"))
        for sentence in _SENTENCE_END.split(prose):
            words = [w for w in sentence.split() if _WORD.search(w)]
            if len(words) > MAX_WORDS:
                errors.append((line, f"{len(words)} words, max {MAX_WORDS}: \"{' '.join(words[:8])} ...\""))
        for kind, pattern in _BANNED.items():
            for match in pattern.finditer(prose):
                errors.append((line, f"{kind}: \"{match.group(0)}\""))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("docs", nargs="*", type=Path, help="docs to check (default: every tracked AGENTS.md and SKILL.md)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failed = False
    for doc in args.docs or doc_files(root):
        for line, message in check_text((root / doc).read_text(encoding="utf-8")):
            failed = True
            print(f"::error file={doc.as_posix()},line={line}::{message}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
