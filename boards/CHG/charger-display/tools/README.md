# Display tools

`build.py` builds demo, monitor or explicit control mode without flashing. It
reads the existing Arduino core, keeps configuration/dependency caches local and
checksum-verifies the pinned display dependency. The shared Arduino library is
resolved from the sibling `boards/CHG/lib` project directory; paths are recomputed rather
than hardcoded to one user's machine.

`monitor.py` reads the USB debug UART for a bounded interval. Select the current
serial port; the historical macOS port is only a default. Avoid attaching a
serial monitor during an active controller session until the adapter's reset
behavior has been checked.

Run from the repository root:

```sh
python3 boards/CHG/charger-display/tools/monitor.py --port /dev/cu.usbserial-110 --seconds 10
```

See the [board README](../README.md) for build modes and hardware notes.
