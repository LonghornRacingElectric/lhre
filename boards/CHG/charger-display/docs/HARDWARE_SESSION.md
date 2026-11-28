# Hardware session — 2026-10-24

Identified over USB: ESP32-D0WD-V3 revision 3.1, 40MHz crystal, 4MB flash,
JEDEC manufacturer C4 / device 6016. USB serial adapter VID1A86/PID7523.
The physical LCD/touch board identity is inferred as LCDWIKI E32R40T from the
Hosyond specification; the serial chip identification alone cannot verify LCD
wiring or touch calibration.

Initial port `/dev/cu.usbserial-10`. Bulk readback attempts with the original
cable produced corrupt serial packets. A very slow ROM read was cancelled.
The user replaced the USB cable; the same ESP32 then appeared at
`/dev/cu.usbserial-110` and was identified successfully.

No valid backup was obtained before flashing. The failed zero-byte `.partial` file was removed; it was not a backup. All pre-flash operations
were flash reads or RAM-only programming-stub transfers; none erased flash.

The requested demo uses no UART2 transmission and no charge-control outputs.

The demo was flashed and each image passed esptool write verification. Initial
TFT_eSPI hardware output was white while the app ran stably. A standalone Arduino
SPI color test succeeded. Firmware now uses TFT_eSPI only as an offscreen drawing
engine and transfers its RGB565 tiles through standard Arduino SPI; touch uses
the same bus through an explicit XPT2046 path and four-corner calibration.

The user confirmed: **dashboard visible and fills screen**. This resolves the
black strip observed with the earlier portrait color test. Display glass marking
reported by user: HSD-9190J-A3; ESP module marking ESP32-32E. This does not
independently establish a motherboard revision. No STM32 or HV hardware was
flashed or activated. Final runtime reports saved calibration (`touch=1`), stable free heap254668 bytes
and274ms frame rendering. The user confirmed touch and tab navigation work. Live UART commissioning
with the custom STM32 controller remains outstanding.
