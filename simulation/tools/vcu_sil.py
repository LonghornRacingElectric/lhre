import csv
import os
import subprocess
import sys
import time
from pathlib import Path

SIM = Path(__file__).resolve().parents[1]
BUILD_DIR = SIM / "out/build/VehicleSimFw"
EXECUTABLE = "LhreVcu.VehicleSimFw"
LIBRARY_DIR = SIM / "modelica/LhreVcu/Resources/Library"
OUT_DIR = SIM / "out/vcu-sil"
STOP_TIME_S = 5.0
STRAIGHT_LINE = {"vcu.useMode": 1, "vcu.steerAmp": 0}
TORQUE_TOLERANCE_NM = 1e-6
SPEED_TOLERANCE_M_S = 1e-3
REJECTED_OVERRIDE_TEXT = ("not possible to override", "not found in model")


def read_result(path: Path) -> list[dict[str, float]]:
    with path.open(newline="") as result_file:
        return [{name: float(value) for name, value in row.items()} for row in csv.DictReader(result_file)]


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
    # Update these two checks when boards/VCU/App gets a control law (boards/VCU/Sil/README.md).
    peak_firmware_torque = max(abs(row["vcu.tau_fw"]) for row in rows)
    if peak_firmware_torque > TORQUE_TOLERANCE_NM:
        failures.append(
            f"The firmware commanded {peak_firmware_torque:g} Nm. The App has no control law yet, so expected 0 Nm."
        )
    if rows[-1]["velX"] > rows[0]["velX"] + SPEED_TOLERANCE_M_S:
        failures.append(f"The car sped up from {rows[0]['velX']:g} to {rows[-1]['velX']:g} m/s with no drive torque.")
    return failures


def simulation_command(result_path: Path, stop_time_s: float) -> list[str]:
    overrides = ",".join(f"{name}={value}" for name, value in STRAIGHT_LINE.items())
    return [
        f"./{EXECUTABLE}",
        f"-override={overrides}",
        f"-stopTime={stop_time_s}",
        f"-r={result_path}",
        "-s=dassl",
        "-jacobian=internalNumerical",
        "-noEventEmit",
        "-lv=LOG_STATS",
    ]


def run_simulation(stop_time_s: float) -> Path:
    executable = BUILD_DIR / EXECUTABLE
    if not executable.is_file():
        raise FileNotFoundError(f"{executable} does not exist. Run make bobsim-fw.")
    if not (LIBRARY_DIR / "libvcu_sil.so").is_file():
        raise FileNotFoundError(f"{LIBRARY_DIR}/libvcu_sil.so does not exist. Run make vcu-sil.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = OUT_DIR / "VehicleSimFw.csv"
    result_path.unlink(missing_ok=True)
    started = time.monotonic()
    completed = subprocess.run(
        simulation_command(result_path, stop_time_s),
        cwd=BUILD_DIR,
        env={**os.environ, "LD_LIBRARY_PATH": str(LIBRARY_DIR)},
        capture_output=True,
        text=True,
        check=False,
    )
    wall_time_s = time.monotonic() - started
    log = completed.stdout + completed.stderr
    (OUT_DIR / "run.log").write_text(log)
    print(f"Simulated {stop_time_s:g} s in {wall_time_s:.1f} s wall time. Log: {OUT_DIR / 'run.log'}")

    if completed.returncode != 0:
        raise RuntimeError(f"{EXECUTABLE} exited with {completed.returncode}. See {OUT_DIR / 'run.log'}.")
    rejected = [line for line in log.splitlines() if any(text in line for text in REJECTED_OVERRIDE_TEXT)]
    if rejected:
        raise RuntimeError(f"The simulation ignored an override: {rejected[0]}")
    return result_path


def main() -> int:
    try:
        result_path = run_simulation(STOP_TIME_S)
    except (FileNotFoundError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1

    rows = read_result(result_path)
    failures = check_run(rows, STOP_TIME_S)
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print(
        f"OK: {len(rows)} rows in {result_path}. velX {rows[0]['velX']:.3f} -> {rows[-1]['velX']:.3f} m/s, "
        f"peak firmware torque {max(abs(row['vcu.tau_fw']) for row in rows):g} Nm."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
