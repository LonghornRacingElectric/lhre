"""Tests the executables emitted by openocd_flash_target and dfu_flash_target.

Flashing needs hardware, so BUILD.bazel instantiates both rules with
flash_probe.py standing in for the flash script (and for OpenOCD / dfu-util).
What's under test is the part the rules own: the launcher has to start the
tool, hand it rlocation paths it can resolve, forward the user's arguments,
and report the tool's exit code — a failed `bazel run //boards/X:openocd` has
to fail the VS Code task or monitor session that ran it.
"""

import json
import os
import subprocess
import unittest

from runfiles import Runfiles

# Env var holding each launcher's rlocation path -> how many files the rule
# hands its tool (openocd + elf + cfg; dfu-util + bin).
LAUNCHERS = {
    "OPENOCD_LAUNCHER": 3,
    "DFU_LAUNCHER": 2,
}


def run_launcher(env_var, *forwarded):
    launcher = Runfiles.Create().Rlocation(os.environ[env_var])
    return subprocess.run(
        [launcher, "--", *forwarded],
        stdout=subprocess.PIPE,
        text=True,
    )


class FlashLauncherTest(unittest.TestCase):
    def test_tool_can_resolve_every_path_the_rule_passes(self):
        for env_var, file_count in LAUNCHERS.items():
            with self.subTest(env_var):
                result = run_launcher(env_var)
                self.assertEqual(result.returncode, 0)
                report = json.loads(result.stdout)
                self.assertEqual(report["resolved"], [True] * file_count)

    def test_user_arguments_are_forwarded_intact(self):
        for env_var in LAUNCHERS:
            with self.subTest(env_var):
                result = run_launcher(env_var, "plain", "two words")
                self.assertEqual(result.returncode, 0)
                report = json.loads(result.stdout)
                self.assertEqual(report["forwarded"], ["plain", "two words"])

    def test_tool_exit_code_is_propagated(self):
        for env_var in LAUNCHERS:
            with self.subTest(env_var):
                result = run_launcher(env_var, "--exit=7")
                self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
