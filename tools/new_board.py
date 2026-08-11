"""Scaffold a board's BUILD.bazel and VS Code debug config from its CubeMX .ioc.

Usage:  bazel run //tools:new_board -- boards/<Name>/<Name>.ioc

Boards should never be created by copying another board's BUILD file — that
copies whatever is incidental to that board along with what's essential.
Everything a BUILD file needs to state is derivable from the .ioc: the MCU
(everything part-coupled — family, device define, linker/startup scripts —
is derived from it by firmware_project) and which middleware CubeMX has
enabled. This script reads exactly that and emits the minimal call.

The same fact also derives the board's on-target debug setup (see
tools/debug/README.md): the script stages the device's SVD file next to the
board and adds the board's Cortex-Debug launch configs and build/flash
tasks to .vscode/. Those entries are keyed by name/label, so re-running the
script refreshes them; pass --vscode-only to do just that for a board whose
BUILD.bazel already exists.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request


def ioc_values(path):
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep:
                values[key] = value
    return values


def mcu_from_device_id(device_id):
    # ProjectManager.DeviceId is the orderable part (STM32G474VETx: pin
    # count + flash size + package); firmware_project wants the die-level
    # header spelling (stm32g474xx). Chars 0-8 name the die; the tail is
    # replaced by the "xx" wildcard, keeping an explicit flash-size letter
    # for families that encode it in the define (e.g. STM32F051x8).
    m = re.match(r"STM32([A-Z]\d{3})", device_id)
    if not m:
        sys.exit(f"error: can't parse an STM32 device from DeviceId '{device_id}'")
    return f"stm32{m.group(1).lower()}xx"


# ---------------------------------------------------------------------------
# VS Code debug setup (see tools/debug/README.md for the architecture)
# ---------------------------------------------------------------------------

# SVDs are never checked in: tools/debug/svd_lock.bzl pins each device's
# file in ST's Apache-2.0 CMSIS-SVD pack (modm-io mirror) by commit+sha256,
# and Bazel downloads them into bazel-bin/tools/debug/svd/ (see
# tools/debug/svd.bzl). This script's job is only to add the lock entry —
# a one-time, network-needing step per device.

SVD_LOCK_TEMPLATE = '''"""Pinned SVD downloads — managed by tools/new_board.py, do not edit by hand.

One entry per debugged device, keyed by the die name launch.json uses.
`path` is the file inside the modm-io/cmsis-svd-stm32 repo (ST's Apache-2.0
CMSIS-SVD pack, mirrored; some families name files with wildcard digits,
e.g. STM32F051 → STM32F0x1.svd — new_board.py resolves that). The commit
pins every entry; bumping it requires re-running
`bazel run //tools:new_board -- <ioc> --vscode-only` per board to refresh
the hashes.
"""

SVD_REPO = "{repo}"

SVD_REPO_COMMIT = "{commit}"

SVD_LOCK = {entries}
'''


def load_svd_lock(lock_path):
    ns = {}
    with open(lock_path, encoding="utf-8") as f:
        exec(f.read(), ns)  # trusted, script-generated file of literals
    return ns["SVD_REPO"], ns["SVD_REPO_COMMIT"], ns["SVD_LOCK"]


def write_svd_lock(lock_path, repo, commit, lock):
    if lock:
        lines = ["{\n"]
        for device in sorted(lock):
            lines.append(f'    "{device}": {{\n')
            lines.append(f'        "path": "{lock[device]["path"]}",\n')
            lines.append(f'        "sha256": "{lock[device]["sha256"]}",\n')
            lines.append("    },\n")
        lines.append("}")
        entries = "".join(lines)
    else:
        entries = "{}"
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(SVD_LOCK_TEMPLATE.format(repo=repo, commit=commit, entries=entries))


def svd_name_candidates(device):
    """The device's SVD file names to probe, most likely first.

    ST names most SVDs after the die (STM32G474.svd) but groups some lines
    under a wildcard digit (STM32F051 → STM32F0x1.svd, STM32L476 →
    STM32L4x6.svd), so probe the exact name and then each single digit
    replaced by 'x' (never the family letter), rightmost-but-one first to
    match ST's usual grouping.
    """
    return [device] + [device[:i] + "x" + device[i + 1:] for i in (7, 8, 6)]


def ensure_svd_locked(repo_root, device, family):
    """Pins the device's SVD in svd_lock.bzl; True if pinned (or already)."""
    lock_path = os.path.join(repo_root, "tools", "debug", "svd_lock.bzl")
    repo, commit, lock = load_svd_lock(lock_path)
    if device in lock:
        print(f"svd: {device} already pinned ({lock[device]['path']})")
        return True

    for candidate in svd_name_candidates(device):
        path = f"{family}/{candidate}.svd"
        url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        except (urllib.error.URLError, OSError) as e:
            print(f"svd: NOT pinned — can't reach {url} ({e}); re-run with network,")
            print(f"  or add the {device} entry to tools/debug/svd_lock.bzl by hand")
            return False
        if not data.lstrip().startswith(b"<?xml"):
            continue
        lock[device] = {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
        write_svd_lock(lock_path, repo, commit, lock)
        print(f"svd: pinned {path} @ {commit[:12]} in tools/debug/svd_lock.bzl")
        return True

    print(f"svd: NOT pinned — no candidate name for {device} exists upstream; browse")
    print(f"  https://github.com/{repo}/tree/{commit}/{family} and add the entry")
    print("  to tools/debug/svd_lock.bzl by hand")
    return False


def load_jsonc(path):
    """Reads VS Code JSON, tolerating the //-comment lines this script emits."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        lines = [l for l in f if not l.lstrip().startswith("//")]
    return json.loads("".join(lines))


def write_vscode_json(path, header_lines, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in header_lines:
            f.write("// " + line + "\n")
        json.dump(data, f, indent=2)
        f.write("\n")


def upsert(entries, key, new_entries):
    """Replaces entries matching new ones by `key`, appends the rest."""
    for new in new_entries:
        for i, old in enumerate(entries):
            if old.get(key) == new[key]:
                entries[i] = new
                break
        else:
            entries.append(new)


def debug_launch_configs(board, name, device, family):
    def config(request):
        cfg = {
            "name": f"{'Debug' if request == 'launch' else 'Attach'} {board} (ST-Link)",
            "type": "cortex-debug",
            "request": request,
            "cwd": "${workspaceFolder}",
            "executable": f"${{workspaceFolder}}/bazel-bin/boards/{board}/{name}.elf",
            "device": device,
            "svdFile": f"${{workspaceFolder}}/bazel-bin/tools/debug/svd/{device}.svd",
            "servertype": "openocd",
            "serverpath": "${workspaceFolder}/bazel-bin/tools/debug/openocd/bin/openocd",
            "gdbPath": "${workspaceFolder}/bazel-bin/tools/debug/gdb/bin/arm-none-eabi-gdb",
            "searchDir": ["${workspaceFolder}/bazel-bin/tools/debug/openocd/openocd/scripts"],
            "configFiles": [f"${{workspaceFolder}}/drivers/stm32/{family}/{family}_openocd.cfg"],
            "preLaunchTask": f"build-{name}-debug",
            "liveWatch": {"enabled": True, "samplesPerSecond": 4},
            "windows": {
                "gdbPath": "${workspaceFolder}/bazel-bin/tools/debug/gdb/bin/arm-none-eabi-gdb.exe",
                "serverpath": "${workspaceFolder}/bazel-bin/tools/debug/openocd/bin/openocd.exe",
            },
        }
        if request == "launch":
            cfg["runToEntryPoint"] = "main"
        return cfg

    return [config("launch"), config("attach")]


def debug_tasks(board, name):
    return [
        {
            "label": f"build-{name}-debug",
            "detail": f"Build {board} firmware and stage the hermetic debug tools",
            "type": "shell",
            "command": "bazel",
            "args": ["build", f"//boards/{board}:{name}.elf", "//tools/debug:debug_tools"],
            "group": "build",
            "problemMatcher": ["$gcc"],
        },
        {
            "label": f"flash-{name}",
            "detail": f"Flash the {board} over ST-Link without starting a debug session",
            "type": "shell",
            "command": "bazel",
            "args": ["run", f"//boards/{board}:openocd"],
            "problemMatcher": [],
        },
    ]


def setup_vscode(repo_root, board, name, mcu):
    device = mcu[:9].upper()  # stm32g474xx → STM32G474, matching ST's SVD names
    family = mcu[:7]

    ensure_svd_locked(repo_root, device, family)

    cfg_rel = f"drivers/stm32/{family}/{family}_openocd.cfg"
    if not os.path.isfile(os.path.join(repo_root, cfg_rel)):
        print(f"note: {cfg_rel} does not exist yet — the debug/flash configs")
        print("  reference it; add it when bringing up the family (see the G4 one)")

    launch_path = os.path.join(repo_root, ".vscode", "launch.json")
    launch = load_jsonc(launch_path) or {"version": "0.2.0", "configurations": []}
    upsert(launch.setdefault("configurations", []), "name", debug_launch_configs(board, name, device, family))
    write_vscode_json(
        launch_path,
        [
            "Board debug configs are managed by tools/new_board.py, which",
            "regenerates them by name — see tools/debug/README.md. Hand-written",
            "entries with other names are left alone (comments are not).",
        ],
        launch,
    )
    print(f"vscode: updated .vscode/launch.json (Debug/Attach {board})")

    tasks_path = os.path.join(repo_root, ".vscode", "tasks.json")
    tasks = load_jsonc(tasks_path) or {"version": "2.0.0", "tasks": []}
    upsert(tasks.setdefault("tasks", []), "label", debug_tasks(board, name))
    write_vscode_json(
        tasks_path,
        [
            "Board build/flash tasks are managed by tools/new_board.py, which",
            "regenerates them by label — see tools/debug/README.md. Hand-written",
            "entries with other labels are left alone (comments are not).",
        ],
        tasks,
    )
    print(f"vscode: updated .vscode/tasks.json (build-{name}-debug, flash-{name})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ioc", help="path to the board's .ioc file (in boards/<Name>/)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing BUILD.bazel")
    parser.add_argument(
        "--vscode-only",
        action="store_true",
        help="skip BUILD.bazel; only (re)generate the VS Code debug setup",
    )
    args = parser.parse_args()

    # `bazel run` starts in the runfiles tree; resolve the user's path from
    # where they invoked us.
    workdir = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())
    ioc_path = os.path.join(workdir, args.ioc) if not os.path.isabs(args.ioc) else args.ioc
    if not os.path.isfile(ioc_path):
        sys.exit(f"error: {args.ioc} not found")

    board_dir = os.path.dirname(ioc_path)
    board = os.path.basename(board_dir)
    # `bazel run` exports the workspace root; fall back to the layout fact
    # that boards live at <root>/boards/<Name> when run as a plain script.
    repo_root = os.environ.get(
        "BUILD_WORKSPACE_DIRECTORY", os.path.dirname(os.path.dirname(board_dir))
    )
    build_path = os.path.join(board_dir, "BUILD.bazel")
    if not args.vscode_only and os.path.exists(build_path) and not args.force:
        sys.exit(f"error: {build_path} already exists (pass --force to overwrite)")

    values = ioc_values(ioc_path)
    mcu = mcu_from_device_id(values.get("ProjectManager.DeviceId", ""))
    ips = {v for k, v in values.items() if re.fullmatch(r"Mcu\.IP\d+", k)}
    freertos = "FREERTOS" in ips
    usb = "USB_DEVICE" in ips

    name = board.lower()
    if not args.vscode_only:
        opts = [f'    name = "{name}",\n']
        if freertos:
            opts.append("    enable_freertos = True,\n")
        if usb:
            opts.append("    enable_usb = True,\n")
        opts.append(f'    mcu = "{mcu}",\n')

        with open(build_path, "w", encoding="utf-8") as f:
            f.write(
                'load("//tools/firmware:firmware_project.bzl", "firmware_project")\n'
                "\n"
                "# The whole board: firmware image, flash targets, and — synthesized\n"
                "# from the App/ file names — the app library, host tests, and sims.\n"
                "# Everything else is derived from mcu or the CubeMX layout; see\n"
                "# tools/firmware/README.md. State only what this board adds.\n"
                "firmware_project(\n" + "".join(opts) + ")\n"
            )

        print(f"wrote {os.path.relpath(build_path, workdir)}  (mcu={mcu}, freertos={freertos}, usb={usb})")

    setup_vscode(repo_root, board, name, mcu)

    if not args.vscode_only:
        print("next steps:")
        print(f"  - write App/{name}_app.cpp/.hpp, App/{name}_app_test.cpp, and Board/main.cpp")
        print(f"    (boards/VCU is the reference; App/ file names become targets)")
        print(f"  - add //boards/{board}:release to //boards:all_firmware")
        print(f"  - write boards/{board}/README.md (see AGENTS.md — docs are part of the change)")


if __name__ == "__main__":
    main()
