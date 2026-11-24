"""Decides which CI areas a change touches, for the `Changes` job in ci.yml.

Reads the changed paths on stdin, one per line (renamed files' old paths
included), and prints one `<area>=true|false` line per area, the format
GitHub's $GITHUB_OUTPUT takes. See README.md for what each area gates.
"""

import argparse
import re
import sys

# The workflow and this classifier decide what runs, so a change to either
# runs every path-gated job and shows the whole effect on its own PR.
_SELF = r"\.github/workflows/ci\.yml$|tools/ci/changes\.py$"

# Area -> paths that make its jobs run, matched from the start of the path.
AREAS = {
    "sim": r"simulation/|vehicle/|\.gitmodules$",
    "runner": r"apps/job_runner/",
    "car": r"vehicle/|simulation/bobsim$|simulation/tools/|simulation/Makefile$|simulation/docker-compose\.yml$",
    "autonomy": r"autonomy/|simulation/bobsim$|vehicle/",
    "bevo": r"apps/BEVO/|lib/spec/|lib/codegen/telemetry/|platforms/|toolchains/|MODULE\.bazel$|MODULE\.bazel\.lock$",
    "docs": r".*\.md$|docs/|mkdocs\.yml$|pyproject\.toml$|uv\.lock$",
    "telemetry": r"apps/telemetry/|apps/BEVO/|lib/spec/|lib/codegen/telemetry/",
    "viewer": r"apps/telemetry/|apps/BEVO/|lib/spec/|lib/codegen/telemetry/",
}

# Areas built by Bazel, which never reads the viewer's npm app.
_BAZEL_AREAS = {"bazel", "telemetry"}

# Bazel ignores these (.bazelignore); every other path is a Bazel input.
_OUTSIDE_BAZEL = re.compile(r"(autonomy|simulation)/")

_VIEWER = "apps/telemetry/analysis/database/viewer_tool/"
# Inside the viewer, Bazel still reads Python (//apps/telemetry/analysis:
# analysis_lib globs **/*.py, which takes in get_schema.py) and would read
# a BUILD or .bzl file, since that changes the package and its globs.
_VIEWER_BAZEL_INPUT = re.compile(r".*(\.py|/BUILD|/BUILD\.bazel|\.bzl)$")


def viewer_only(path):
    """True for a viewer file no Bazel target reads."""
    return path.startswith(_VIEWER) and not _VIEWER_BAZEL_INPUT.match(path)


def classify(paths, event, everything=False):
    """Returns {area: bool}. `event` is the GitHub event name."""
    paths = [p for p in paths if p]
    # No list means the API answer was odd, not that nothing changed.
    everything = everything or not paths
    bazel_paths = [p for p in paths if not viewer_only(p)]
    out = {"bazel": everything or any(not _OUTSIDE_BAZEL.match(p) for p in bazel_paths)}
    for area, pattern in AREAS.items():
        regex = re.compile(f"(?:{pattern}|{_SELF})")
        candidates = bazel_paths if area in _BAZEL_AREAS else paths
        out[area] = everything or any(regex.match(p) for p in candidates)
    # The weekly run leaves FourPost to simulation.yml's full regeneration.
    if event == "schedule":
        out["car"] = False
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, help="github.event_name")
    parser.add_argument("--everything", action="store_true", help="run every area")
    args = parser.parse_args(argv)
    paths = sys.stdin.read().splitlines()
    for area, on in classify(paths, args.event, args.everything).items():
        print(f"{area}={'true' if on else 'false'}")


if __name__ == "__main__":
    main()
