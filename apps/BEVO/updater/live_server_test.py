"""Opt-in copy/reconciliation test against a real deployed authority and GHCR."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from apps.BEVO.updater.oci import fetch


class LiveServerTest(unittest.TestCase):
    def test_available_ghcr_release_installs_against_the_running_server(self):
        url = os.environ.get("BEVO_SERVER_URL")
        self.assertTrue(url, "set BEVO_SERVER_URL to the deployed authority's profile.json URL")
        token = os.environ.get("BEVO_SERVER_TOKEN")
        headers = {"Authorization": "Bearer " + token} if token else {}
        before = json.loads(fetch(url, 2 * 1024 * 1024, headers))
        self.assertEqual(len(before.get("deployment", {}).get("consumers", [])), 2,
                         "the live test requires the authority's --deployed mode")
        self.assertTrue(before["releases"], "approve a published digest before testing a fresh install")
        with tempfile.TemporaryDirectory(prefix="bevo-live-updater-") as directory:
            archive = str(Path(sys.argv[1]).resolve())
            command = [sys.executable, "-I", "-S", archive, "update", "--root", directory, "--server-url", url]
            installed = subprocess.run(command, capture_output=True, text=True, timeout=300)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            result = json.loads(installed.stdout)
            self.assertEqual(result["action"], "install")
            active = (Path(directory) / "current").resolve()
            plan = json.loads((active / "reconciliation.json").read_bytes())
            self.assertEqual(plan["server"]["schema_id"], before["schema"]["schema_id"])
            self.assertEqual(json.loads(fetch(url, 2 * 1024 * 1024, headers)), before,
                             "server deployment changed during the live test")
            kept = subprocess.run(command, capture_output=True, text=True, timeout=300)
            self.assertEqual(kept.returncode, 0, kept.stderr)
            self.assertEqual(json.loads(kept.stdout)["action"], "keep")
            self.assertEqual((Path(directory) / "current").resolve(), active)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
