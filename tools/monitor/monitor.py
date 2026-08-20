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
import time
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


def write_paced(fd, data, gap_s=0.005):
    """Writes one byte per gap. RTOS boards poll their shell every few ms
    with only a few bytes of UART buffering, so a full-speed burst would
    overrun; human typing is naturally paced, but this probe isn't."""
    for i in range(len(data)):
        os.write(fd, data[i : i + 1])
        time.sleep(gap_s)


# ANSI color escape codes for high-visibility terminal banners
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_RED = "\033[1;31m"
CLR_GREEN = "\033[1;32m"
CLR_YELLOW = "\033[1;33m"
CLR_CYAN = "\033[1;36m"


def print_banner_box(title, color, lines):
    """Prints a prominent, formatted banner box with ANSI colors."""
    use_color = sys.stdout.isatty()
    c_hdr = color if use_color else ""
    c_bold = CLR_BOLD if use_color else ""
    c_rst = CLR_RESET if use_color else ""
    width = 72
    border = "=" * width
    print(f"\n{c_hdr}{border}{c_rst}")
    print(f"{c_hdr}{c_bold}  {title}{c_rst}")
    print(f"{c_hdr}{'-' * width}{c_rst}")
    for line in lines:
        print(f"  {line}")
    print(f"{c_hdr}{border}{c_rst}\n")


def check_version(fd, root):
    """Sends /version and compares the firmware sha against the checkout.

    Best-effort: prints its verdict and returns True when firmware
    provably matches the checkout, False otherwise.
    """
    write_paced(fd, b"\r/version\r")
    reply = read_for(fd, 1.0)
    match = BANNER_RE.search(reply)
    if match is None:
        print_banner_box(
            "✗ ERROR: NO /version RESPONSE FROM BOARD",
            CLR_RED,
            [
                "Could not communicate with the debug shell on the board.",
                "",
                "Troubleshooting checklist:",
                "  • Check that the board is plugged in and powered",
                "  • Verify baud rate (expected 115200)",
                "  • Ensure no other serial monitor (e.g. screen, minicom) is open",
                "  • Verify firmware includes longhorn::Shell",
            ],
        )
        return False

    # Show the human-readable banner line the match came from.
    line_start = reply.rfind(b"\n", 0, match.start()) + 1
    line_end = reply.find(b"\r", match.end())
    banner = reply[line_start : line_end if line_end != -1 else None]
    banner_text = banner.decode(errors="replace").strip()

    fw_sha = match.group(1).decode()
    fw_dirty = match.group(2) is not None
    if fw_sha == "unknown":
        print_banner_box(
            "⚠ WARNING: UNSTAMPED FIRMWARE BUILD",
            CLR_YELLOW,
            [
                f"Board Banner:  {banner_text}",
                "Firmware was built without git stamping (SHA is unknown).",
                "Cannot compare against workspace HEAD.",
            ],
        )
        return False
    if root is None:
        print_banner_box(
            "⚠ WARNING: NOT IN A GIT CHECKOUT",
            CLR_YELLOW,
            [
                f"Board Banner:  {banner_text}",
                f"Board SHA:     {fw_sha}",
                "Not inside a git repository; cannot verify firmware against HEAD.",
            ],
        )
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
        print_banner_box(
            "✗ FIRMWARE OUT OF DATE!",
            CLR_RED,
            [
                f"Board Banner:  {banner_text}",
                f"Board SHA:     {fw_sha}{' (-dirty)' if fw_dirty else ''}",
                f"Workspace SHA: {head[:12]}{' (dirty)' if ws_dirty else ''}",
                "",
                "Action Required:",
                "  The board is running old firmware. Reflash before testing:",
                "  • Bazel CLI:  bazel run //tools/monitor -- --flash //boards/<Board>:openocd",
                "  • VS Code:    Terminal → Run Task → flash-and-monitor-<name>",
            ],
        )
        return False
    if fw_dirty or ws_dirty:
        reasons = []
        if fw_dirty:
            reasons.append("firmware was built with uncommitted changes (-dirty)")
        if ws_dirty:
            reasons.append("local workspace has uncommitted changes")
        print_banner_box(
            "⚠ WARNING: UNCOMMITTED CHANGES (MATCH UNVERIFIABLE)",
            CLR_YELLOW,
            [
                f"Board Banner:  {banner_text}",
                f"Board SHA:     {fw_sha}{' (-dirty)' if fw_dirty else ''}",
                f"Workspace SHA: {head[:12]}{' (dirty)' if ws_dirty else ''}",
                f"Reason:        {'; '.join(reasons)}",
                "",
                "Note: Commit hash matches HEAD, but exact byte identity cannot",
                "be guaranteed due to dirty working tree state.",
            ],
        )
        return False
    print_banner_box(
        "✓ FIRMWARE UP TO DATE (MATCHES CHECKOUT)",
        CLR_GREEN,
        [
            f"Board Banner:  {banner_text}",
            f"Board SHA:     {fw_sha}",
            f"Workspace SHA: {head[:12]} (clean)",
            "Status:        Firmware matches current HEAD exactly. Ready to go!",
        ],
    )
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
