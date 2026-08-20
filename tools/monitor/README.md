# monitor

Serial console for boards running the
[longhorn shell](../../drivers/longhorn/README.md): connect to the board's
ST-LINK virtual COM port, verify the flashed firmware matches your
checkout, and talk to it interactively.

```bash
bazel run //tools/monitor                                     # connect to the one attached board
bazel run //tools/monitor -- --flash //boards/VCU:openocd     # flash via ST-Link first, then connect
bazel run //tools/monitor -- --flash //boards/VCU:dfu         # flash via USB DFU first, then connect
bazel run //tools/monitor -- --check                          # verify only; exit 1 if stale
```

You can also run the **`flash-and-monitor-<name>`** task from VS Code's Task Runner,
which will prompt you to choose between ST-Link (OpenOCD) and DFU before connecting.

On connect it sends `/version` and compares the sha the board reports
against `git rev-parse HEAD`:

- mismatch: warns `FIRMWARE OUT OF DATE` with both shas
- same commit but dirty (either side): says an exact match can't be proven
- match and clean: confirms and moves on

Then it's a raw console: what you type goes to the board (the shell echoes
and supports backspace), `/help` lists commands, `Ctrl-]` quits.

Flags: `--port` when auto-detection finds zero or several candidates
(`/dev/cu.usbmodem*`, `/dev/ttyACM*`), `--baud` (default 115200, matching
the boards' debug UARTs).

Gotchas:

- One process per port. A leftover `screen` session makes open fail with
  a busy error (`screen -ls` to find it).
- Type, don't paste. Boards poll their shell every few ms with a few
  bytes of UART buffering, so pasting a long line can drop characters
  (the `/version` probe paces itself for this reason).
- On macOS use the printed `cu.*` device, never `tty.*` (blocks on open).
- Stdlib only, POSIX only. On Windows use PuTTY on the same COM port; the
  version check just won't happen.
