"""Serial monitor for the longhorn debug shell.

Opens a board's ST-LINK virtual COM port, asks the firmware for /version,
compares the reported sha against the checkout, and drops into an
interactive console (Ctrl-] quits). Optionally flashes first:

    bazel run //tools/monitor                                   # just connect
    bazel run //tools/monitor -- --flash //boards/VCU:openocd   # flash, then connect
    bazel run //tools/monitor -- --check                        # version check only

Stdlib only, so `python3 tools/monitor/monitor.py` also works. Cross-platform:
termios on POSIX, Win32 via ctypes on Windows (use Windows Terminal; legacy
conhost renders the UI poorly).
"""

import argparse
import glob
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import select
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


# --- Serial port backends ---------------------------------------------------
#
# One class per OS with the same three methods. read() blocks until the first
# byte or the timeout: bytes when data arrived, None on timeout, b"" when the
# port is gone (board unplugged).


class PosixSerial:
    def __init__(self, path, baud):
        if baud not in BAUD_CONSTANTS:
            sys.exit(
                "error: unsupported baud %d (supported: %s)"
                % (baud, ", ".join(str(b) for b in sorted(BAUD_CONSTANTS)))
            )
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            hint = ""
            if e.errno == 16:  # EBUSY: usually a forgotten screen session
                hint = " (something else has it open; `screen -ls`?)"
            sys.exit(f"error: cannot open {path}: {e.strerror}{hint}")

        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0  # raw: no iflag/oflag/lflag processing
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # 8N1
        attrs[4] = attrs[5] = BAUD_CONSTANTS[baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def read(self, max_bytes, timeout_s):
        readable, _, _ = select.select([self.fd], [], [], timeout_s)
        if not readable:
            return None
        try:
            return os.read(self.fd, max_bytes)  # b"" = EOF, port gone
        except OSError:
            return b""

    def write(self, data):
        os.write(self.fd, data)

    def close(self):
        os.close(self.fd)


if os.name == "nt":

    # Fixed-width field types (not wintypes.DWORD, which is only 32-bit on
    # Windows) so the layout is provably 28/20 bytes, the Win32 ABI sizes.
    class _Dcb(ctypes.Structure):
        _fields_ = [
            ("DCBlength", ctypes.c_uint32),
            ("BaudRate", ctypes.c_uint32),
            ("fFlags", ctypes.c_uint32),  # the DCB bitfield, as one DWORD
            ("wReserved", ctypes.c_uint16),
            ("XonLim", ctypes.c_uint16),
            ("XoffLim", ctypes.c_uint16),
            ("ByteSize", ctypes.c_ubyte),
            ("Parity", ctypes.c_ubyte),
            ("StopBits", ctypes.c_ubyte),
            ("XonChar", ctypes.c_char),
            ("XoffChar", ctypes.c_char),
            ("ErrorChar", ctypes.c_char),
            ("EofChar", ctypes.c_char),
            ("EvtChar", ctypes.c_char),
            ("wReserved1", ctypes.c_uint16),
        ]

    class _CommTimeouts(ctypes.Structure):
        _fields_ = [
            ("ReadIntervalTimeout", ctypes.c_uint32),
            ("ReadTotalTimeoutMultiplier", ctypes.c_uint32),
            ("ReadTotalTimeoutConstant", ctypes.c_uint32),
            ("WriteTotalTimeoutMultiplier", ctypes.c_uint32),
            ("WriteTotalTimeoutConstant", ctypes.c_uint32),
        ]

    # Explicit prototypes: ctypes' default int return type truncates 64-bit
    # HANDLEs, which would break the INVALID_HANDLE_VALUE check.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    for _fn, _args in (
        ("GetCommState", (wintypes.HANDLE, ctypes.c_void_p)),
        ("SetCommState", (wintypes.HANDLE, ctypes.c_void_p)),
        ("SetCommTimeouts", (wintypes.HANDLE, ctypes.c_void_p)),
        ("SetupComm", (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)),
        (
            "ReadFile",
            (
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ),
        ),
        (
            "WriteFile",
            (
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ),
        ),
        ("CloseHandle", (wintypes.HANDLE,)),
        ("GetConsoleMode", (wintypes.HANDLE, ctypes.c_void_p)),
        ("SetConsoleMode", (wintypes.HANDLE, wintypes.DWORD)),
    ):
        _proto = getattr(_k32, _fn)
        _proto.restype = wintypes.BOOL
        _proto.argtypes = _args

    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _MAXDWORD = 0xFFFFFFFF

    class WindowsSerial:
        def __init__(self, name, baud):
            # The \\.\ prefix is required for COM10 and up; harmless below.
            path = name if name.startswith("\\\\") else "\\\\.\\" + name
            self.handle = _k32.CreateFileW(
                path,
                0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
                0,
                None,
                3,  # OPEN_EXISTING
                0,
                None,
            )
            if self.handle in (None, _INVALID_HANDLE):
                err = ctypes.get_last_error()
                hint = ""
                if err == 5:  # ERROR_ACCESS_DENIED: usually another terminal
                    hint = " (something else has it open; PuTTY/Arduino IDE?)"
                sys.exit(f"error: cannot open {name} (Win32 error {err}){hint}")

            dcb = _Dcb()
            dcb.DCBlength = ctypes.sizeof(_Dcb)
            if not _k32.GetCommState(self.handle, ctypes.byref(dcb)):
                sys.exit(f"error: {name} is not a serial port")
            dcb.BaudRate = baud
            dcb.ByteSize = 8
            dcb.Parity = 0  # NOPARITY
            dcb.StopBits = 0  # ONESTOPBIT
            # fBinary | fDtrControl=ENABLE | fRtsControl=ENABLE, everything
            # else (parity checks, flow control, XON/XOFF) off — matches the
            # raw termios setup in PosixSerial.
            dcb.fFlags = 0x0001 | 0x0010 | 0x1000
            if not _k32.SetCommState(self.handle, ctypes.byref(dcb)):
                sys.exit(f"error: cannot configure {name} at {baud} baud")
            _k32.SetupComm(self.handle, 4096, 4096)
            self._timeout_ms = None

        def _set_read_timeout(self, timeout_ms):
            if timeout_ms == self._timeout_ms:
                return
            # Interval and multiplier both MAXDWORD + a total constant means:
            # block until the first byte or the constant expires, then return
            # whatever is buffered — the same shape as select() + read().
            t = _CommTimeouts()
            t.ReadIntervalTimeout = _MAXDWORD
            t.ReadTotalTimeoutMultiplier = _MAXDWORD
            t.ReadTotalTimeoutConstant = max(1, timeout_ms)
            t.WriteTotalTimeoutMultiplier = 0
            t.WriteTotalTimeoutConstant = 1000
            _k32.SetCommTimeouts(self.handle, ctypes.byref(t))
            self._timeout_ms = timeout_ms

        def read(self, max_bytes, timeout_s):
            self._set_read_timeout(int(timeout_s * 1000))
            buf = ctypes.create_string_buffer(max_bytes)
            n = wintypes.DWORD()
            ok = _k32.ReadFile(
                self.handle, buf, max_bytes, ctypes.byref(n), None
            )
            if not ok:
                return b""  # port gone (board unplugged)
            if n.value == 0:
                return None
            return buf.raw[: n.value]

        def write(self, data):
            n = wintypes.DWORD()
            if not _k32.WriteFile(
                self.handle, data, len(data), ctypes.byref(n), None
            ):
                raise OSError("serial write failed")

        def close(self):
            _k32.CloseHandle(self.handle)

    def enable_vt():
        """Turns on ANSI escape processing (Windows Terminal has it on
        already; legacy conhost needs the nudge)."""
        _k32.GetStdHandle.restype = wintypes.HANDLE
        _k32.GetStdHandle.argtypes = (wintypes.DWORD,)
        for std_handle in (-11, -12):  # stdout, stderr
            h = _k32.GetStdHandle(wintypes.DWORD(std_handle).value)
            mode = wintypes.DWORD()
            if _k32.GetConsoleMode(h, ctypes.byref(mode)):
                _k32.SetConsoleMode(
                    h, mode.value | 0x0004
                )  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def open_port(path, baud):
    if os.name == "nt":
        return WindowsSerial(path, baud)
    return PosixSerial(path, baud)


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
    if os.name == "nt":
        # Every present COM port registers itself here; no pyserial needed.
        import winreg

        candidates = []
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM"
            )
            i = 0
            while True:
                try:
                    _, port, _ = winreg.EnumValue(key, i)
                    candidates.append(port)
                    i += 1
                except OSError:
                    break
        except OSError:
            pass
        looked_for = "the SERIALCOMM registry (COMx)"
    else:
        candidates = [p for pattern in PORT_PATTERNS for p in glob.glob(pattern)]
        looked_for = ", ".join(PORT_PATTERNS)

    if not candidates:
        sys.exit(
            "error: no serial port found (looked for %s); is the board "
            "plugged in? Use --port to point at one explicitly." % looked_for
        )
    if len(candidates) > 1:
        sys.exit(
            "error: multiple serial ports found, pick one with --port:\n  "
            + "\n  ".join(sorted(candidates))
        )
    return candidates[0]


def flash(label, root):
    if root is None:
        sys.exit("error: --flash needs a workspace (run via bazel run, or from the repo)")
    print(f"flashing: bazel run {label}")
    result = subprocess.run(["bazel", "run", label], cwd=root)
    if result.returncode != 0:
        sys.exit(f"error: flash failed (exit {result.returncode})")


def read_for(port, seconds):
    """Collects whatever arrives on the port within the window."""
    buf = b""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        data = port.read(4096, remaining)
        if data:
            buf += data
        elif data == b"":  # port gone
            break
    return buf


def write_paced(port, data, gap_s=0.005):
    """Writes one byte per gap. RTOS boards poll their shell every few ms
    with only a few bytes of UART buffering, so a full-speed burst would
    overrun; human typing is naturally paced, but this probe isn't."""
    for i in range(len(data)):
        port.write(data[i : i + 1])
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


def check_version(port, root):
    """Sends /version and compares the firmware sha against the checkout.

    Best-effort: prints its verdict and returns True when firmware
    provably matches the checkout, False otherwise.
    """
    write_paced(port, b"\r/version\r")
    reply = read_for(port, 1.0)
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


# --- Event sources ----------------------------------------------------------
#
# Windows select() only understands sockets, so instead of multiplexing the
# port and stdin, two daemon threads feed one queue of ("serial", bytes) /
# ("key", bytes) events and the UI loop consumes it. A b"" payload means the
# source closed.


def serial_reader(port, events):
    while True:
        try:
            data = port.read(4096, 0.05)
        except OSError:
            events.put(("serial", b""))
            return
        if data is None:
            continue
        events.put(("serial", data))
        if not data:
            return


def posix_key_reader(events):
    fd = sys.stdin.fileno()
    while True:
        try:
            data = os.read(fd, 4096)
        except OSError:
            data = b""
        events.put(("key", data))
        if not data:
            return


def windows_key_reader(events):
    while True:
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):  # arrows/function keys: two-byte pairs
            msvcrt.getch()
            continue
        events.put(("key", ch))


def start_event_threads(port, interactive):
    events = queue.Queue()
    threading.Thread(
        target=serial_reader, args=(port, events), daemon=True
    ).start()
    if os.name == "nt" and interactive:
        key_target = windows_key_reader
    else:
        key_target = posix_key_reader  # piped stdin works the same on both
    threading.Thread(target=key_target, args=(events,), daemon=True).start()
    return events


def interact(port):
    is_tty = os.isatty(sys.stdin.fileno()) and sys.stdout.isatty()

    if not is_tty:
        # Fallback for piped stdin/stdout: raw byte forwarding, no UI.
        print("monitor: connected (piped mode).")
        sys.stdout.flush()  # the loop below writes to the binary layer
        events = start_event_threads(port, interactive=False)
        stdin_open = True
        try:
            while True:
                kind, data = events.get()
                if kind == "serial":
                    if not data:
                        return
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                elif stdin_open:
                    if not data:  # piped stdin ran out; keep showing output
                        stdin_open = False
                        continue
                    port.write(data)
        except (KeyboardInterrupt, OSError):
            return

    # Interactive TTY mode: split pane with scrolling logs and bottom text box
    if os.name == "nt":
        enable_vt()
        saved = None
    else:
        stdin = sys.stdin.fileno()
        saved = termios.tcgetattr(stdin)
        tty.setraw(stdin)
    cols, rows = shutil.get_terminal_size((80, 24))
    init_terminal_ui(rows, cols)

    events = start_event_threads(port, interactive=True)
    paused = False
    paused_buffer = bytearray()
    rx_buffer = bytearray()
    input_chars = []

    try:
        while True:
            # The timeout doubles as the idle tick: flush partial lines and
            # notice terminal resizes (there is no SIGWINCH on Windows).
            try:
                kind, data = events.get(timeout=0.02)
            except queue.Empty:
                kind, data = None, None
            idle = kind is None

            if idle:
                new_cols, new_rows = shutil.get_terminal_size((80, 24))
                if (new_cols, new_rows) != (cols, rows):
                    cols, rows = new_cols, new_rows
                    init_terminal_ui(rows, cols)
                    draw_status_bar(rows, cols, paused, len(paused_buffer))
                    draw_prompt(rows, "".join(input_chars))
                    sys.stdout.flush()

            if kind == "serial":
                if not data:
                    write_scroll_area(
                        "\r\nmonitor: port closed (board unplugged?)\r\n",
                        rows,
                        "".join(input_chars),
                    )
                    return
                rx_buffer.extend(data)

            # Flush buffered serial data on newlines or idle
            if rx_buffer and (b"\n" in rx_buffer or idle):
                if paused:
                    # When paused, buffer timestamped logs and let command responses through
                    text = rx_buffer.decode("utf-8", errors="replace")
                    if idle or text.endswith("\n"):
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

            if kind == "key":
                raw_in = data
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
                            write_paced(port, (cmd + "\r").encode())
                        else:
                            port.write(b"\r")
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
        reset_terminal_ui(rows)
        if saved is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
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

    port_name = args.port or find_port()
    port = open_port(port_name, args.baud)
    print(f"monitor: {port_name} @ {args.baud}")
    try:
        up_to_date = check_version(port, root)
        if args.check:
            sys.exit(0 if up_to_date else 1)
        interact(port)
    finally:
        port.close()


if __name__ == "__main__":
    main()
