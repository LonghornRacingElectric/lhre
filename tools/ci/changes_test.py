"""Pins which CI jobs a change runs. A wrong `false` silently skips a check
that should have caught something, so each skip here is deliberate."""

import contextlib
import io
import unittest
from unittest import mock

from tools.ci import changes

VIEWER = "apps/telemetry/analysis/database/viewer_tool/"
ALL_AREAS = {"bazel", "sim", "runner", "car", "autonomy", "bevo", "docs", "telemetry", "viewer"}


def on(paths, event="pull_request", everything=False):
    """The areas that run, as a set."""
    return {a for a, v in changes.classify(paths, event, everything).items() if v}


class ViewerOnlyTest(unittest.TestCase):
    def test_viewer_source_runs_only_the_viewer(self):
        # PRs #309 and #311: the npm app only. Bazel never reads it.
        paths = [VIEWER + "src/components/ui/button.tsx", VIEWER + "package-lock.json"]
        self.assertEqual(on(paths), {"viewer"})

    def test_viewer_readme_also_runs_docs(self):
        self.assertEqual(on([VIEWER + "README.md"]), {"viewer", "docs"})

    def test_viewer_python_still_runs_bazel_and_telemetry(self):
        # analysis_lib globs **/*.py, so get_schema.py is in every image.
        self.assertEqual(on([VIEWER + "get_schema.py"]), {"bazel", "telemetry", "viewer"})

    def test_build_file_in_viewer_runs_bazel(self):
        for name in ("BUILD", "BUILD.bazel", "defs.bzl"):
            with self.subTest(name=name):
                self.assertIn("bazel", on([VIEWER + "tools/" + name]))
                self.assertIn("telemetry", on([VIEWER + "tools/" + name]))

    def test_python_renamed_out_of_viewer_runs_bazel(self):
        # The old path comes in the list too, so a move is seen from both ends.
        self.assertIn("bazel", on([VIEWER + "old.py", "apps/telemetry/analysis/new.py"]))

    def test_viewer_plus_other_telemetry_runs_everything_telemetry(self):
        paths = [VIEWER + "src/app/page.tsx", "apps/telemetry/stack/ingest/BUILD.bazel"]
        self.assertEqual(on(paths), {"bazel", "telemetry", "viewer"})

    def test_lookalike_prefix_is_not_the_viewer(self):
        self.assertIn("bazel", on(["apps/telemetry/analysis/database/viewer_tool_old/x.ts"]))

    def test_rest_of_telemetry_runs_the_viewer_too(self):
        # The viewer job still runs on what it ran on before (spec, BEVO).
        for path in ("lib/spec/messages.textproto", "apps/BEVO/dashd/main.cc"):
            with self.subTest(path=path):
                self.assertTrue({"telemetry", "viewer"} <= on([path]))


class AreasTest(unittest.TestCase):
    def test_simulation_only_skips_bazel(self):
        self.assertEqual(on(["simulation/tools/car.sh"]), {"sim", "car"})

    def test_autonomy_only_skips_bazel(self):
        self.assertEqual(on(["autonomy/ros2/src/x.py"]), {"autonomy"})

    def test_vehicle_runs_sim_car_and_autonomy(self):
        self.assertEqual(on(["vehicle/vehicle.yml"]), {"bazel", "sim", "car", "autonomy"})

    def test_markdown_anywhere_runs_docs(self):
        self.assertEqual(on(["simulation/docs/tests.md"]), {"sim", "docs"})

    def test_firmware_runs_bazel_only(self):
        self.assertEqual(on(["boards/VCU/App/torque.cpp"]), {"bazel"})

    def test_module_bazel_runs_bevo(self):
        self.assertEqual(on(["MODULE.bazel.lock"]), {"bazel", "bevo"})

    def test_job_runner(self):
        self.assertEqual(on(["apps/job_runner/server.mjs"]), {"bazel", "runner"})

    def test_workflow_and_classifier_run_everything(self):
        for path in (".github/workflows/ci.yml", "tools/ci/changes.py"):
            with self.subTest(path=path):
                self.assertEqual(on([path]), ALL_AREAS)

    def test_other_workflow_runs_bazel_only(self):
        self.assertEqual(on([".github/workflows/docs.yml"]), {"bazel"})


class EventTest(unittest.TestCase):
    def test_everything_runs_every_area(self):
        self.assertEqual(on([], event="workflow_dispatch", everything=True), ALL_AREAS)

    def test_schedule_leaves_four_post_out(self):
        self.assertEqual(on([], event="schedule", everything=True), ALL_AREAS - {"car"})

    def test_schedule_never_runs_four_post(self):
        self.assertNotIn("car", on(["vehicle/vehicle.yml"], event="schedule"))

    def test_empty_list_runs_everything(self):
        self.assertEqual(on(["", ""]), ALL_AREAS)


class MainTest(unittest.TestCase):
    def run_main(self, stdin, *args):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
            changes.main(list(args))
        return out.getvalue().splitlines()

    def test_prints_github_output_lines(self):
        lines = self.run_main(VIEWER + "src/app/page.tsx\n", "--event", "pull_request")
        self.assertEqual(len(lines), len(ALL_AREAS))
        self.assertIn("viewer=true", lines)
        self.assertIn("bazel=false", lines)
        self.assertIn("telemetry=false", lines)

    def test_everything_flag(self):
        lines = self.run_main("", "--event", "push", "--everything")
        self.assertEqual(sorted(lines), sorted(f"{a}=true" for a in ALL_AREAS))


if __name__ == "__main__":
    unittest.main()
