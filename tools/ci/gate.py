"""The single required PR check: fails unless every job that had to run passed.

A job may be skipped only when the Changes job ran and said its area is
untouched. Any other skip, failure, cancellation or missing job fails the gate.
"""

import json
import os
import sys

# Job id -> (display name, Changes output that decides whether it must run).
# None means it always runs.
JOBS = {
    "format": ("Format", None),
    "bazel": ("Firmware · Bazel (Linux)", "bazel"),
    "bazel-windows": ("Firmware · Bazel (Windows)", "bazel"),
    "sim-fast": ("Simulation · fast checks", "sim"),
    "four-post": ("Simulation · FourPost", "car"),
    "autonomy": ("Autonomy · colcon", "autonomy"),
    "bevo": ("BEVO · Pi binaries", "bevo"),
    "docs": ("Docs · build", "docs"),
}


def check(needs):
    """Returns (rows, errors). Each row is (name, ok, detail)."""
    rows = []
    errors = []
    changes = needs.get("changes", {})
    detected = changes.get("result") == "success"
    if not detected:
        detail = f"{changes.get('result', 'missing')}: no job may be skipped"
        rows.append(("Changes", False, detail))
        errors.append(f"Changes: {detail}")
    outputs = (changes.get("outputs") or {}) if detected else {}

    unknown = sorted(set(needs) - set(JOBS) - {"changes"})
    for job in list(JOBS) + unknown:
        name, area = JOBS.get(job, (job, None))
        result = needs.get(job, {}).get("result", "missing")
        required = area is None or outputs.get(area) != "false"
        if result == "success":
            ok, detail = True, "passed"
        elif result == "skipped" and not required:
            ok, detail = True, f"skipped: no change to its paths ({area})"
        elif result == "skipped":
            ok, detail = False, "skipped, but it had to run"
        else:
            ok, detail = False, result
        rows.append((name, ok, detail))
        if not ok:
            errors.append(f"{name}: {detail}")
    return rows, errors


def summary(rows, run_url):
    lines = ["### CI", "", "| Check | Result |", "|---|---|"]
    for name, ok, detail in rows:
        mark = "✅" if ok and detail == "passed" else "⏭️" if ok else "❌"
        lines.append(f"| {name} | {mark} {detail} |")
    lines += ["", f"Job logs: {run_url}"]
    return "\n".join(lines) + "\n"


def main():
    rows, errors = check(json.loads(os.environ["NEEDS"]))
    text = summary(rows, os.environ.get("RUN_URL", ""))
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    for error in errors:
        print(f"::error title=CI gate::{error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
