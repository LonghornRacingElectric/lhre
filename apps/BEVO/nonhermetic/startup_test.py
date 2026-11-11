import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class StartupTest(unittest.TestCase):
    def test_modem_then_sync_then_all_core_processes_use_the_new_release(self):
        self.startup("success")

    def test_missing_updater_starts_the_existing_release(self):
        self.startup("missing")

    def test_failed_updater_starts_the_existing_release(self):
        self.startup("failure")

    def startup(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "order.log"
            def executable(path, source):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
                path.chmod(0o755)
            for version in ("old", "new"):
                for daemon in ("cand", "loggerd", "publishd"):
                    executable(root / version / "bin" / daemon,
                               '#!/bin/sh\necho "$0" >> "$ORDER_LOG"\nsleep 30\n')
            executable(root / "bin/dashd", '#!/bin/sh\necho dashd >> "$ORDER_LOG"\nsleep 30\n')
            executable(root / "bin/ip", '#!/bin/sh\nexit 0\n')
            executable(root / "gps", '#!/bin/sh\necho GPS >> "$ORDER_LOG"\n')
            (root / "cell.py").write_text('import os\nwith open(os.environ["ORDER_LOG"], "a") as f: f.write("MODEM\\n")\n')
            (root / "current").symlink_to(root / "old")
            (root / "updater.py").write_text(
                'import os\nfrom pathlib import Path\n'
                'root=Path(os.environ["BEVO_CORE_ROOT"])\n'
                'with open(os.environ["ORDER_LOG"], "a") as f: f.write("SYNC\\n")\n'
                '(root/"current").unlink()\n(root/"current").symlink_to(root/"new")\n')
            env = dict(os.environ, ORDER_LOG=str(log), BEVO_CORE_ROOT=str(root),
                       BEVO_BIN_DIR=str(root / "bin"), BEVO_PYTHON_BIN=sys.executable,
                       BEVO_CELL_SCRIPT=str(root / "cell.py"), BEVO_LOCUS_POWER_SCRIPT=str(root / "gps"),
                       BEVO_UPDATER=str(root / "updater.py"), BEVO_SERVER_URL="https://authority.invalid/profile.json",
                       PATH=str(root / "bin") + os.pathsep + os.environ["PATH"])
            env.pop("BEVO_CORE_RELEASE", None)
            if mode == "missing":
                (root / "updater.py").unlink()
            elif mode == "failure":
                (root / "updater.py").write_text('raise RuntimeError("updater unavailable")\n')
            selected = "/new/bin/" if mode == "success" else "/old/bin/"
            process = subprocess.Popen(["bash", str(Path(sys.argv[1]).resolve())], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    lines = log.read_text().splitlines() if log.exists() else []
                    if sum(selected in line for line in lines) == 3:
                        break
                    if process.poll() is not None:
                        self.fail(process.stdout.read().decode())
                    time.sleep(.05)
                else:
                    self.fail("core processes did not start: " + str(lines))
                if mode == "success":
                    self.assertLess(lines.index("MODEM"), lines.index("SYNC"))
                    self.assertTrue(all(lines.index(line) > lines.index("SYNC") for line in lines if selected in line))
                self.assertFalse(any(("/old/bin/" if mode == "success" else "/new/bin/") in line for line in lines))
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
