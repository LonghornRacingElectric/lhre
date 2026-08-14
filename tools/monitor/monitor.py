"""Serial monitor for the longhorn debug shell.

Opens a board's ST-LINK virtual COM port, asks the firmware for /version,
compares the reported sha against the checkout, and drops into an
interactive console (Ctrl-] quits). Optionally flashes first:

    bazel run //tools/monitor                                   # just connect
    bazel run //tools/monitor -- --flash //boards/VCU:openocd   # flash, then connect
    bazel run //tools/monitor -- --check                        # version check only

Stdlib only (termios), so `python3 tools/monitor/monitor.py` also works.
POSIX only: on Windows use PuTTY against the same COM port.
"""

import argparse
import glob
import os
import re
import select
import subprocess
import sys
import termios
import tty

BAUD_CONSTANTS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
    230400: termios.B230400,
}

# The shell's banner: "<board> <describe> (<sha12>[-dirty])". The sha is
# "unknown" on unstamped builds, so [0-9a-f] alone would miss it.
BANNER_RE = re.compile(rb"\(([0-9a-f]{7,40}|unknown)(-dirty)?\)")

QUIT_BYTE = 0x1D  # Ctrl-]

PORT_PATTERNS = (
    "/dev/cu.usbmodem*",  # macOS (cu, not tty: tty blocks on carrier detect)
    "/dev/ttyACM*",  # Linux
)


def workspace_root():
    # Set by `bazel run`; fall back to git for direct invocation.
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root:
        return root
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    return out.stdout.strip() if out.returncode == 0 else None


def find_port():
    candidates = [p for pattern in PORT_PATTERNS for p in glob.glob(pattern)]
    if not candidates:
        sys.exit(
            "error: no serial port found (looked for %s); is the board "
            "plugged in? Use --port to point at one explicitly."
            % ", ".join(PORT_PATTERNS)
        )
    if len(candidates) > 1:
        sys.exit(
            "error: multiple serial ports found, pick one with --port:\n  "
            + "\n  ".join(candidates)
        )
    return candidates[0]


def flash(label, root):
    if root is None:
        sys.exit("error: --flash needs a workspace (run via bazel run, or from the repo)")
    print(f"flashing: bazel run {label}")
    result = subprocess.run(["bazel", "run", label], cwd=root)
    if result.returncode != 0:
        sys.exit(f"error: flash failed (exit {result.returncode})")


def open_port(path, baud):
    if baud not in BAUD_CONSTANTS:
        sys.exit(
            "error: unsupported baud %d (supported: %s)"
            % (baud, ", ".join(str(b) for b in sorted(BAUD_CONSTANTS)))
        )
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        hint = ""
        if e.errno == 16:  # EBUSY: usually a forgotten screen session
            hint = " (something else has it open; `screen -ls`?)"
        sys.exit(f"error: cannot open {path}: {e.strerror}{hint}")

    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0  # raw: no iflag/oflag/lflag processing
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # 8N1
    attrs[4] = attrs[5] = BAUD_CONSTANTS[baud]
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def read_for(fd, seconds):
    """Collects whatever arrives on fd within the window."""
    buf = b""
    deadline = os.times().elapsed + seconds
    while (remaining := deadline - os.times().elapsed) > 0:
        readable, _, _ = select.select([fd], [], [], remaining)
        if readable:
            buf += os.read(fd, 4096)
    return buf


def check_version(fd, root):
    """Sends /version and compares the firmware sha against the checkout.

    Best-effort: prints its verdict and returns True when firmware
    provably matches the checkout, False otherwise.
    """
    os.write(fd, b"\r/version\r")
    reply = read_for(fd, 1.0)
    match = BANNER_RE.search(reply)
    if match is None:
        print(
            "monitor: no /version response; firmware without the shell, "
            "wrong baud, or a port hog?"
        )
        return False

    # Show the human-readable banner line the match came from.
    line_start = reply.rfind(b"\n", 0, match.start()) + 1
    line_end = reply.find(b"\r", match.end())
    banner = reply[line_start : line_end if line_end != -1 else None]
    print(f"monitor: board reports: {banner.decode(errors='replace').strip()}")

    fw_sha = match.group(1).decode()
    fw_dirty = match.group(2) is not None
    if fw_sha == "unknown":
        print("monitor: firmware build was unstamped; cannot compare")
        return False
    if root is None:
        print("monitor: not in a git checkout; cannot compare")
        return False

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    ws_dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )

    if not head.startswith(fw_sha):
        print(
            f"monitor: FIRMWARE OUT OF DATE: board has {fw_sha}, checkout "
            f"is at {head[:12]}. Reflash (e.g. --flash //boards/...:openocd)."
        )
        return False
    if fw_dirty or ws_dirty:
        # Same commit, but uncommitted changes on either side make byte
        # identity unknowable from the sha alone.
        print(
            f"monitor: sha matches HEAD ({fw_sha}) but "
            f"{'firmware' if fw_dirty else 'checkout'} has uncommitted "
            "changes; exact match not verifiable"
        )
        return False
    print(f"monitor: firmware matches checkout ({fw_sha})")
    return True


def interact(fd):
    print("monitor: connected. Ctrl-] quits. Try /help.")
    stdin = sys.stdin.fileno()
    is_tty = os.isatty(stdin)
    saved = termios.tcgetattr(stdin) if is_tty else None
    if is_tty:
        tty.setraw(stdin)
    watch_stdin = True
    try:
        while True:
            sources = [fd, stdin] if watch_stdin else [fd]
            readable, _, _ = select.select(sources, [], [])
            if fd in readable:
                data = os.read(fd, 4096)
                if not data:
                    print("\r\nmonitor: port closed (board unplugged?)")
                    return
                os.write(sys.stdout.fileno(), data)
            if stdin in readable:
                data = os.read(stdin, 4096)
                if not data:  # piped stdin ran out; keep showing output
                    watch_stdin = False
                    continue
                if is_tty and QUIT_BYTE in data:
                    print("\r\nmonitor: bye")
                    return
                os.write(fd, data)
    except KeyboardInterrupt:
        print("\r\nmonitor: bye")
    except OSError:
        print("\r\nmonitor: port error (board unplugged?)")
    finally:
        if saved is not None:
            termios.tcsetattr(stdin, termios.TCSADRAIN, saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial device (default: auto-detect)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--flash", metavar="LABEL", help="bazel run this flash target first"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="version-check only, no interactive console; exit 1 on mismatch",
    )
    args = parser.parse_args()

    root = workspace_root()
    if args.flash:
        flash(args.flash, root)

    port = args.port or find_port()
    fd = open_port(port, args.baud)
    print(f"monitor: {port} @ {args.baud}")
    try:
        up_to_date = check_version(fd, root)
        if args.check:
            sys.exit(0 if up_to_date else 1)
        interact(fd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
