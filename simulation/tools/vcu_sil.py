import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

from tools.invalidation import GitUnavailable, Layout, git, git_root, sha256_file
from tools.vcu_sil_report import VARIABLES, driver_demand_nm, variable_filter, write_report
from tools.vcu_sil_tracks import TRACKS, Track, track_errors, write_driver_trace

SIM = Path(__file__).resolve().parents[1]
BUILD_DIR = SIM / "out/build/VehicleSimFw"
EXECUTABLE = "LhreVcu.VehicleSimFw"
LIBRARY_DIR = SIM / "modelica/LhreVcu/Resources/Library"
OUT_DIR = SIM / "out/vcu-sil"
TORQUE_TOLERANCE_NM = 1e-6
REJECTED_OVERRIDE_TEXT = ("not possible to override", "not found in model")


def read_result(path: Path) -> list[dict[str, float]]:
    with path.open(newline="") as result_file:
        return [{name: float(value) for name, value in row.items()} for row in csv.DictReader(result_file)]


def missing_variables(rows: list[dict[str, float]]) -> list[str]:
    return [name for name in VARIABLES if name not in rows[0]]


def check_run(rows: list[dict[str, float]], stop_time_s: float) -> list[str]:
    if not rows:
        return ["The result has no rows."]
    if rows[-1]["time"] < stop_time_s - 1e-9:
        return [f"The simulation stopped at {rows[-1]['time']:g} s, before {stop_time_s:g} s. See run.log."]

    failures = []
    over_command = [row["time"] for row in rows if row["vcu.tau_cmd_limited"] > row["vcu.tau_fw"] + TORQUE_TOLERANCE_NM]
    if over_command:
        failures.append(
            f"The plant got more torque than the firmware commanded, first at {over_command[0]:g} s. "
            "BobLib's own torque command may still reach the inverter."
        )
    outside_demand = [
        row
        for row in rows
        if not -TORQUE_TOLERANCE_NM <= row["vcu.tau_fw"] <= driver_demand_nm(row) + TORQUE_TOLERANCE_NM
    ]
    if outside_demand:
        first = outside_demand[0]
        failures.append(
            f"The firmware commanded {first['vcu.tau_fw']:g} Nm at {first['time']:g} s, outside 0 to the driver demand "
            f"{driver_demand_nm(first):g} Nm. Traction control must only remove torque."
        )
    return failures


def run_dir(track_name: str, traction_control: bool) -> Path:
    return OUT_DIR / (track_name if traction_control else f"{track_name}--tc-off")


def simulation_command(result_path: Path, track: Track, trace_path: Path, traction_control: bool) -> list[str]:
    overrides = {
        "initialVel": track.initial_speed_m_s,
        **track.steer,
        "vcu.driverTraceFile": trace_path,
        "vcu.tractionControl": str(traction_control).lower(),
    }
    override = ",".join(f"{name}={value}" for name, value in overrides.items())
    return [
        f"./{EXECUTABLE}",
        f"-override={override}",
        f"-stopTime={track.stop_time_s}",
        f"-r={result_path}",
        "-s=dassl",
        "-jacobian=internalNumerical",
        "-noEventEmit",
        "-lv=LOG_STATS",
        "-emit_protected",
        f"-variableFilter={variable_filter()}",
    ]


def _commit(repo: Path | None) -> str:
    if repo is None:
        return "unknown (bobsim/.gitmodules names no BobLib submodule)"
    try:
        root = git_root(repo)
        sha = git(root, "rev-parse", "--short", "HEAD").strip()
        changes = git(root, "status", "--porcelain", "--ignore-submodules=dirty").strip()
    except GitUnavailable as error:
        return f"unknown ({error})"
    return f"{sha}+dirty" if changes else sha


def run_provenance(layout: Layout, library: Path) -> dict[str, str]:
    return {
        "lhre": _commit(layout.repo),
        "BobSim": _commit(layout.bobsim),
        "BobLib": _commit(layout.boblib),
        "libvcu_sil.so sha256": sha256_file(library)[:12],
    }


def run_simulation(out_dir: Path, track: Track, traction_control: bool) -> Path:
    executable = BUILD_DIR / EXECUTABLE
    if not executable.is_file():
        raise FileNotFoundError(f"{executable} does not exist. Run make bobsim-fw.")
    if not (LIBRARY_DIR / "libvcu_sil.so").is_file():
        raise FileNotFoundError(f"{LIBRARY_DIR}/libvcu_sil.so does not exist. Run make vcu-sil.")

    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "driver.csv"
    write_driver_trace(trace_path, track)
    result_path = out_dir / "VehicleSimFw.csv"
    result_path.unlink(missing_ok=True)
    started = time.monotonic()
    completed = subprocess.run(
        simulation_command(result_path, track, trace_path, traction_control),
        cwd=BUILD_DIR,
        env={**os.environ, "LD_LIBRARY_PATH": str(LIBRARY_DIR)},
        capture_output=True,
        text=True,
        check=False,
    )
    wall_time_s = time.monotonic() - started
    log = completed.stdout + completed.stderr
    (out_dir / "run.log").write_text(log)
    print(f"Simulated {track.stop_time_s:g} s in {wall_time_s:.1f} s wall time. Log: {out_dir / 'run.log'}")

    if completed.returncode != 0:
        raise RuntimeError(f"{EXECUTABLE} exited with {completed.returncode}. See {out_dir / 'run.log'}.")
    rejected = [line for line in log.splitlines() if any(text in line for text in REJECTED_OVERRIDE_TEXT)]
    if rejected:
        raise RuntimeError(f"The simulation ignored an override: {rejected[0]}")
    return result_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("track", choices=TRACKS)
    parser.add_argument("--tc", choices=("on", "off"), default="on")
    arguments = parser.parse_args(argv)
    track_name = arguments.track
    traction_control = arguments.tc == "on"
    track = TRACKS[track_name]
    errors = track_errors(track)
    for error in errors:
        print(f"FAIL: {track_name}: {error}")
    if errors:
        return 1

    out_dir = run_dir(track_name, traction_control)
    try:
        result_path = run_simulation(out_dir, track, traction_control)
    except (FileNotFoundError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1

    rows = read_result(result_path)
    missing = missing_variables(rows) if rows else []
    if missing:
        print(f"FAIL: The result has no {', '.join(missing)}. A BobLib name may have changed.")
        return 1
    failures = check_run(rows, track.stop_time_s)
    for failure in failures:
        print(f"FAIL: {failure}")
    report_path = out_dir / "report.pdf"
    run_details = {
        "Track": f"{track_name}: {track.description}",
        "Traction control": arguments.tc,
        **run_provenance(Layout.default(), LIBRARY_DIR / "libvcu_sil.so"),
    }
    write_report(report_path, rows, failures, run_details)
    print(f"Report: {report_path}")
    if failures:
        return 1
    print(
        f"OK: {len(rows)} rows in {result_path}. velX {rows[0]['velX']:.3f} -> {rows[-1]['velX']:.3f} m/s, "
        f"peak firmware torque {max(abs(row['vcu.tau_fw']) for row in rows):g} Nm."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
