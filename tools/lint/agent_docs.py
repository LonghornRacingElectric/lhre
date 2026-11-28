"""Checks that agent docs (AGENTS.md, CLAUDE.md, SKILL.md) still match the repo.

An agent follows these files step by step, so a renamed target or a bad
merge sends it the wrong way with no error. This check makes that a CI
failure: numbered steps in order, make targets and Bazel labels that exist,
relative links that resolve, and skill frontmatter that agents can load.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

DOC_NAMES = ("AGENTS.md", "CLAUDE.md", "SKILL.md")

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^#{1,6}\s")
_ITEM = re.compile(r"^(\s*)(\d+)\.\s")
_SPAN = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_LABEL = re.compile(r"(?<![\w:/.])//([A-Za-z0-9_./-]*)(?::([A-Za-z0-9_./+-]+))?")
_MAKE_RULE = re.compile(r"^([A-Za-z0-9_.-]+(?:[ \t]+[A-Za-z0-9_.-]+)*)[ \t]*:(?!=)")
_SKILL_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def doc_files(root):
    """The agent docs git tracks, as paths relative to `root`."""
    out = subprocess.run(
        ["git", "ls-files", "-z", *(f"*{n}" for n in DOC_NAMES)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(Path(p) for p in out.split("\0") if p and Path(p).name in DOC_NAMES)


def split_fences(text):
    """Yields (line number, line, in a code fence) for each line."""
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        yield n, line, fenced


def check_steps(text):
    """Numbered steps go 1, 2, 3 at each indent. A heading starts a new list."""
    errors = []
    last = {}
    for n, line, fenced in split_fences(text):
        if fenced:
            continue
        if _HEADING.match(line):
            last.clear()
            continue
        item = _ITEM.match(line)
        if not item:
            if line.strip() and not line[0].isspace():
                last.clear()
            continue
        indent, number = len(item.group(1)), int(item.group(2))
        last = {i: v for i, v in last.items() if i <= indent}
        prev = last.get(indent)
        if prev is not None and number not in (prev + 1, 1):
            errors.append((n, f"step {number} follows step {prev}"))
        last[indent] = number
    return errors


def make_commands(text):
    """Yields (line number, make command words) from code spans and fences."""
    for n, line, fenced in split_fences(text):
        chunks = [line] if fenced else _SPAN.findall(line)
        for chunk in chunks:
            for part in re.split(r"&&|\|\||;|\|", chunk):
                words = part.split()
                if words and words[0] == "$":
                    words = words[1:]
                if words and words[0] == "make":
                    yield n, words[1:]


def make_targets(makefile):
    """The rule names a Makefile defines."""
    targets = set()
    for line in makefile.read_text(encoding="utf-8").splitlines():
        rule = _MAKE_RULE.match(line)
        if rule:
            targets.update(t for t in rule.group(1).split() if not t.startswith("."))
    return targets


def makefile_for(root, doc, directory):
    """The Makefile `make` reads: `-C <dir>` from the repo root, else the nearest one above the doc."""
    if directory is not None:
        path = root / directory / "Makefile"
        return path if path.is_file() else None
    for parent in (root / doc).parents:
        if (parent / "Makefile").is_file():
            return parent / "Makefile"
        if parent == root:
            break
    return None


def check_make(root, doc, text):
    errors = []
    cache = {}
    for n, args in make_commands(text):
        directory, targets = None, []
        words = iter(args)
        for word in words:
            if word == "-C":
                directory = next(words, "")
            elif word.startswith("-") or "=" in word:
                continue
            else:
                targets.append(word)
        makefile = makefile_for(root, doc, directory)
        if makefile is None:
            errors.append((n, f"no Makefile for `make {' '.join(args)}`"))
            continue
        if makefile not in cache:
            cache[makefile] = make_targets(makefile)
        rel = makefile.relative_to(root).as_posix()
        errors.extend((n, f"make target `{t}` is not in {rel}") for t in targets if t not in cache[makefile])
    return errors


def check_labels(root, text):
    """Bazel labels in code name a real package, and a target named in its BUILD file."""
    errors = []
    for n, line, fenced in split_fences(text):
        chunks = [line] if fenced else _SPAN.findall(line)
        for chunk in chunks:
            for label in _LABEL.finditer(chunk):
                package, target = label.group(1), label.group(2)
                if package.endswith("..."):
                    if not (root / package[:-3]).is_dir():
                        errors.append((n, f"`{label.group(0)}`: no directory {package[:-3] or '.'}"))
                    continue
                build = next((root / package / b for b in ("BUILD.bazel", "BUILD") if (root / package / b).is_file()), None)
                if build is None:
                    errors.append((n, f"`{label.group(0)}`: no BUILD file in {package or 'the repo root'}"))
                elif target and f'name = "{target}"' not in build.read_text(encoding="utf-8"):
                    errors.append((n, f"`{label.group(0)}`: no target named {target} in {build.relative_to(root).as_posix()}"))
    return errors


def check_links(root, doc, text):
    """Relative links resolve to a file or folder in the repo."""
    errors = []
    for n, line, fenced in split_fences(text):
        if fenced:
            continue
        for target in _LINK.findall(line):
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            if not (root / doc).parent.joinpath(path).exists():
                errors.append((n, f"link target {path} does not exist"))
    return errors


def check_skill(doc, text):
    """Frontmatter that every Agent Skills loader reads: a name that matches the folder and a description."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return [(1, "a SKILL.md starts with --- frontmatter ---")]
    end = lines.index("---", 1)
    fields = dict(line.split(":", 1) for line in lines[1:end] if ":" in line and not line[0].isspace())
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    errors = []
    name = fields.get("name", "")
    if name != doc.parent.name:
        errors.append((2, f"name `{name}` does not match the folder `{doc.parent.name}`"))
    if not _SKILL_NAME.fullmatch(name) or len(name) > 64:
        errors.append((2, f"name `{name}` is not lowercase-kebab-case of 64 characters or fewer"))
    description = fields.get("description", "")
    if not description or len(description) > 1024:
        errors.append((3, "description is empty or longer than 1024 characters"))
    return errors


def check_doc(root, doc):
    """Returns [(line, message)] for one doc, `doc` relative to `root`."""
    text = (root / doc).read_text(encoding="utf-8")
    errors = check_steps(text) + check_make(root, doc, text) + check_labels(root, text) + check_links(root, doc, text)
    if doc.name == "SKILL.md":
        errors += check_skill(doc, text)
    return sorted(errors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("docs", nargs="*", type=Path, help="docs to check (default: every tracked one)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failed = False
    for doc in args.docs or doc_files(root):
        for line, message in check_doc(root, doc):
            failed = True
            print(f"::error file={doc.as_posix()},line={line}::{message}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
