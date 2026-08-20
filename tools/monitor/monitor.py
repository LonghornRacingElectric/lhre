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
import shutil
import signal
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


def init_terminal_ui(rows, cols):
    """Sets up split-pane terminal: scrolling area for logs, status bar, and input prompt."""
    # Advance past the version banner cleanly
    sys.stdout.write("\r\n")
    sys.stdout.write(f"\033[1;{rows - 2}r")  # Set scrolling region to rows 1..rows-2
    sys.stdout.write(f"\033[{rows - 2};1H")  # Position cursor in scroll region
    sys.stdout.write("\0337")  # Save initial scroll cursor position
    draw_status_bar(rows, cols, paused=False)
    draw_prompt(rows, "")
    sys.stdout.flush()


def reset_terminal_ui(rows):
    """Restores full-screen scrolling and resets cursor position."""
    sys.stdout.write("\033[r")  # Reset scrolling region to full screen
    sys.stdout.write(f"\033[{rows};1H\r\n")  # Move to bottom
    sys.stdout.flush()


def draw_status_bar(rows, cols, paused, buffered_bytes=0):
    """Draws the fixed divider / status bar on line rows-1."""
    status_row = rows - 1
    sys.stdout.write(f"\033[{status_row};1H\033[2K")
    if paused:
        tag = f" ⏸ PAUSED ({buffered_bytes} B held) | Ctrl-P/Ctrl-D: Resume | Enter: Send cmd "
        color = "\033[1;33;40m"  # Bold yellow
    else:
        tag = " ⏺ LIVE STREAM | Ctrl-P/Ctrl-D: Pause Stream | Ctrl-]: Quit "
        color = "\033[1;36;40m"  # Bold cyan

    pad = max(0, cols - len(tag))
    left = pad // 2
    right = pad - left
    bar = f"{color}{'─' * left}{tag}{'─' * right}\033[0m"
    sys.stdout.write(bar[: cols + 20])


def draw_prompt(rows, text):
    """Draws the fixed bottom input line on line rows."""
    sys.stdout.write(f"\033[{rows};1H\033[2K\033[1;32m>\033[0m {text}")


def write_scroll_area(text, rows, input_text):
    """Writes text inside the upper scrolling pane without corrupting the prompt."""
    sys.stdout.write("\0338")  # Restore cursor to scroll region
    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
    sys.stdout.write(normalized)
    sys.stdout.write("\0337")  # Save updated scroll cursor position
    draw_prompt(rows, input_text)
    sys.stdout.flush()


TIMESTAMP_LOG_RE = re.compile(r"^\[\d+\]")


def interact(fd):
    stdin = sys.stdin.fileno()
    is_tty = os.isatty(stdin) and sys.stdout.isatty()
    saved = termios.tcgetattr(stdin) if is_tty else None

    if not is_tty:
        # Fallback for piped stdin/stdout
        print("monitor: connected (piped mode). Ctrl-] quits.")
        watch_stdin = True
        try:
            while True:
                sources = [fd, stdin] if watch_stdin else [fd]
                readable, _, _ = select.select(sources, [], [])
                if fd in readable:
                    data = os.read(fd, 4096)
                    if not data:
                        return
                    os.write(sys.stdout.fileno(), data)
                if stdin in readable:
                    data = os.read(stdin, 4096)
                    if not data:
                        watch_stdin = False
                        continue
                    os.write(fd, data)
        except (KeyboardInterrupt, OSError):
            return

    # Interactive TTY mode: split pane with scrolling logs and bottom text box
    tty.setraw(stdin)
    cols, rows = shutil.get_terminal_size((80, 24))
    init_terminal_ui(rows, cols)

    paused = False
    paused_buffer = bytearray()
    rx_buffer = bytearray()
    input_chars = []

    def on_resize(signum, frame):
        nonlocal rows, cols
        cols, rows = shutil.get_terminal_size((80, 24))
        init_terminal_ui(rows, cols)
        draw_status_bar(rows, cols, paused, len(paused_buffer))
        draw_prompt(rows, "".join(input_chars))
        sys.stdout.flush()

    signal.signal(signal.SIGWINCH, on_resize)

    try:
        while True:
            # Use short timeout so accumulated rx_buffer lines are flushed cleanly
            readable, _, _ = select.select([fd, stdin], [], [], 0.02)

            if fd in readable:
                data = os.read(fd, 4096)
                if not data:
                    write_scroll_area(
                        "\r\nmonitor: port closed (board unplugged?)\r\n",
                        rows,
                        "".join(input_chars),
                    )
                    return
                rx_buffer.extend(data)

            # Flush buffered serial data on newlines or idle
            if rx_buffer and (b"\n" in rx_buffer or not readable):
                if paused:
                    # When paused, buffer timestamped logs and let command responses through
                    text = rx_buffer.decode("utf-8", errors="replace")
                    if not readable or text.endswith("\n"):
                        rx_buffer.clear()
                    else:
                        last_nl = text.rfind("\n")
                        if last_nl != -1:
                            rx_buffer = bytearray(
                                text[last_nl + 1 :].encode("utf-8")
                            )
                            text = text[: last_nl + 1]
                        else:
                            text = ""

                    if text:
                        to_display = []
                        for raw_line in text.splitlines(keepends=True):
                            stripped = raw_line.strip()
                            if TIMESTAMP_LOG_RE.match(stripped):
                                if len(paused_buffer) < 262144:
                                    paused_buffer.extend(
                                        raw_line.encode("utf-8")
                                    )
                            else:
                                to_display.append(raw_line)

                        if to_display:
                            write_scroll_area(
                                "".join(to_display), rows, "".join(input_chars)
                            )

                        draw_status_bar(rows, cols, paused, len(paused_buffer))
                        draw_prompt(rows, "".join(input_chars))
                        sys.stdout.flush()
                else:
                    text = rx_buffer.decode("utf-8", errors="replace")
                    rx_buffer.clear()
                    write_scroll_area(text, rows, "".join(input_chars))

            if stdin in readable:
                raw_in = os.read(stdin, 4096)
                if not raw_in:
                    return

                i = 0
                while i < len(raw_in):
                    b = raw_in[i]

                    # Ctrl-] (0x1D) or Ctrl-C (0x03): Quit
                    if b in (QUIT_BYTE, 0x03):
                        return

                    # Ctrl-P (0x10) or Ctrl-D (0x04): Pause / Resume toggle
                    if b in (0x10, 0x04):
                        paused = not paused
                        if not paused and paused_buffer:
                            flushed = paused_buffer.decode(
                                "utf-8", errors="replace"
                            )
                            paused_buffer.clear()
                            write_scroll_area(
                                flushed, rows, "".join(input_chars)
                            )
                        draw_status_bar(rows, cols, paused, len(paused_buffer))
                        draw_prompt(rows, "".join(input_chars))
                        sys.stdout.flush()
                        i += 1
                        continue

                    # Backspace / DEL
                    if b in (0x08, 0x7F):
                        if input_chars:
                            input_chars.pop()
                            draw_prompt(rows, "".join(input_chars))
                            sys.stdout.flush()
                        i += 1
                        continue

                    # Enter / Return
                    if b in (0x0D, 0x0A):
                        cmd = "".join(input_chars).strip()
                        input_chars.clear()
                        draw_prompt(rows, "")
                        sys.stdout.flush()

                        if cmd:
                            write_paced(fd, (cmd + "\r").encode())
                        else:
                            os.write(fd, b"\r")
                        i += 1
                        continue

                    # Handle arrow keys / escape sequences
                    if b == 0x1B:
                        if i + 2 < len(raw_in) and raw_in[i + 1] == ord("["):
                            i += 3
                            continue
                        i += 1
                        continue

                    # Printable ASCII
                    if 0x20 <= b <= 0x7E:
                        input_chars.append(chr(b))
                        draw_prompt(rows, "".join(input_chars))
                        sys.stdout.flush()

                    i += 1

    except KeyboardInterrupt:
        pass
    except OSError:
        pass
    finally:
        if is_tty:
            reset_terminal_ui(rows)
            if saved is not None:
                termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
            print("monitor: bye")


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
