#!/usr/bin/env python3
"""Quick native charger tests without the Bazel dependency cache."""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[4]
shared = root / "boards/CHG/lib"
flags = ["-Wall", "-Wextra", "-Werror", "-pedantic"]

def run(args):
    subprocess.run([str(arg) for arg in args], check=True)

with tempfile.TemporaryDirectory(prefix="lhre-charger-") as folder:
    out = Path(folder)
    cc, cxx = os.environ.get("CC", "cc"), os.environ.get("CXX", "c++")
    run([cc, "-std=c99", *flags, "-I" + str(shared / "src"), "-c", shared / "src/charger_ui_link.c", "-o", out / "peer.o"])
    run([cxx, "-std=c++11", *flags, "-I" + str(shared / "src"), shared / "test/test_link.cpp", shared / "src/ChargerLink.cpp", out / "peer.o", "-o", out / "protocol"])
    run([out / "protocol"])
    run([cxx, "-std=c++11", *flags, "-I" + str(shared / "src"), shared / "test/test_budget.cpp", "-o", out / "budget"])
    run([out / "budget"])
    app = root / "boards/CHG/App"
    run([cxx, "-std=c++17", *flags, "-I" + str(shared / "src"), "-I" + str(app), "-I" + str(root / "drivers/lhal/include"), app / "chg_app_test.cpp", app / "chg_app.cpp", shared / "src/ChargerLink.cpp", out / "peer.o", "-o", out / "app"])
    run([out / "app"])
