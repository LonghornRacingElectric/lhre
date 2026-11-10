"""Keeps ruff's per-file baseline in ruff.toml honest.

The baseline grandfathers violations that existed when a rule was turned on,
so new code is linted strictly without a repo-wide cleanup diff. It may only
shrink: once a file stops violating a rule, its entry has to go, or the old
exemption would quietly cover the next regression.

Usage (from anywhere in the repo):
  uv run --group lint tools/lint/ruff_baseline.py           # check (CI)
  uv run --group lint tools/lint/ruff_baseline.py --write   # regenerate
  uv run --group lint tools/lint/ruff_baseline.py --no-growth-from origin/main

--no-growth-from also fails if the baseline gained an entry since that git
ref. Regenerating the baseline is the easy way to make new violations pass,
so CI refuses it unless the PR carries the lint-baseline-ok label.
"""

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

BEGIN = "# --- ruff baseline (generated) ---"
END = "# --- end ruff baseline ---"
SECTION = "[lint.per-file-ignores]"


def actual_violations(root: Path) -> dict[str, list[str]]:
    """Every rule each file violates, with the baseline switched off."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--exit-zero",
            "--output-format=json",
            "--config",
            "lint.per-file-ignores = {}",
            ".",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    found: dict[str, set[str]] = {}
    for item in json.loads(result.stdout):
        # Syntax errors have no code and can't be baselined.
        if item["code"] is None:
            continue
        path = Path(item["filename"]).resolve().relative_to(root).as_posix()
        found.setdefault(path, set()).add(item["code"])
    return {path: sorted(codes) for path, codes in sorted(found.items())}


def committed_baseline(config: Path) -> dict[str, list[str]]:
    data = tomllib.loads(config.read_text())
    table = data.get("lint", {}).get("per-file-ignores", {})
    return {path: sorted(codes) for path, codes in table.items()}


def ref_exists(root: Path, ref: str) -> bool:
    found = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    return found.returncode == 0


def baseline_at(root: Path, ref: str) -> dict[str, list[str]] | None:
    """The baseline committed at a git ref, or None if ruff.toml isn't there.

    Only call this for a ref that exists: a missing ref would read as "no
    baseline yet" and skip the growth check.
    """
    shown = subprocess.run(
        ["git", "show", f"{ref}:ruff.toml"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return None
    table = tomllib.loads(shown.stdout).get("lint", {}).get("per-file-ignores", {})
    return {path: sorted(codes) for path, codes in table.items()}


def growth(base: dict[str, list[str]], head: dict[str, list[str]]) -> list[str]:
    """Baseline entries in head that base didn't have."""
    grown = []
    for path, codes in head.items():
        added = sorted(set(codes) - set(base.get(path, [])))
        if added:
            grown.append(f"{path}: {', '.join(added)}")
    return grown


def render(baseline: dict[str, list[str]]) -> str:
    lines = [SECTION]
    for path, codes in baseline.items():
        quoted = ", ".join(f'"{code}"' for code in codes)
        lines.append(f'"{path}" = [{quoted}]')
    return "\n".join(lines) + "\n"


def write(config: Path, baseline: dict[str, list[str]]) -> None:
    text = config.read_text()
    start = text.index(BEGIN)
    body_start = text.index(SECTION, start)
    body_end = text.index(END, body_start)
    config.write_text(text[:body_start] + render(baseline) + text[body_end:])


def check(baseline: dict[str, list[str]], actual: dict[str, list[str]]) -> list[str]:
    """Baseline entries no file needs any more."""
    stale = []
    for path, codes in baseline.items():
        unused = sorted(set(codes) - set(actual.get(path, [])))
        if unused:
            stale.append(f"{path}: {', '.join(unused)}")
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate the baseline")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repo root holding ruff.toml (default: this repo)",
    )
    parser.add_argument(
        "--no-growth-from",
        metavar="REF",
        help="also fail if the baseline gained entries since this git ref",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = root / "ruff.toml"

    # A failed fetch or a typo must not quietly turn the growth check off.
    if args.no_growth_from and not ref_exists(root, args.no_growth_from):
        print(f"error: can't read {args.no_growth_from}; fetch it before checking growth.")
        return 1

    actual = actual_violations(root)
    if args.write:
        write(config, actual)
        print(f"Wrote a baseline of {len(actual)} files to {config.name}.")
        return 0

    committed = committed_baseline(config)
    status = 0
    if args.no_growth_from:
        base = baseline_at(root, args.no_growth_from)
        grown = growth(base, committed) if base is not None else []
        if grown:
            print(f"The ruff baseline grew since {args.no_growth_from}:")
            for line in grown:
                print(f"  {line}")
            print(
                "\nFix these instead of baselining them. A file that only moved\n"
                "keeps its entry under the new name; a maintainer can add the\n"
                "lint-baseline-ok label for that and re-run the job.\n"
            )
            status = 1

    stale = check(committed, actual)
    if stale:
        print("These ruff baseline entries are no longer needed:")
        for line in stale:
            print(f"  {line}")
        print(
            "\nThe baseline only shrinks. Remove them with:\n"
            "  uv run --group lint tools/lint/ruff_baseline.py --write"
        )
        status = 1
    if status == 0:
        print("ruff baseline is up to date.")
    return status


if __name__ == "__main__":
    sys.exit(main())
