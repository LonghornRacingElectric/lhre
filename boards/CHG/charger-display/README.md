# Charger display

ESP32-32E, 480×320 landscape ST7796S LCD and XPT2046 resistive touch. The demo
has been flashed and the user verified a full-screen dashboard, touch calibration
and navigation. This is separate from the [CHG STM32 board](../README.md).

## Layout

- [firmware](firmware/README.md): dashboard and minimal panel diagnostic.
- [include](include/README.md): offscreen font/drawing configuration.
- [tools](tools/README.md): build and USB serial monitor.
- [preview](preview/README.md): independent browser design preview with mock data.
- [CHG library](../lib/README.md): the only copy of the UART protocol.

The dashboard has Live, Checks, Pack, Faults and Control tabs, battery animation,
SOC/SOE/ETA, voltage/current/power, ten readiness checks, temperature, cell delta,
session energy, profile and power-limiting reason. Missing/stale data show `--`.

## Build

From the repository root, with Arduino CLI and ESP32 Arduino core **3.3.8** installed:

```sh
python3 boards/CHG/charger-display/tools/build.py --mode demo
python3 boards/CHG/charger-display/tools/build.py --mode monitor
python3 boards/CHG/charger-display/tools/build.py --mode control
```

For a new checkout, install Python 3.12+ and Arduino CLI first. The build script
requires the ESP32 Arduino core to be installed separately (it does not install
or update your global core):

```sh
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@3.3.8 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

The first build downloads and verifies the pinned display library automatically.

Default `demo` sends no STM32 UART messages. `monitor` reads real telemetry but
cannot request charging. `control` explicitly enables engineering START/STOP
requests and requires a real, validated controller integration. Builds do not
flash hardware. Output is ignored under `boards/CHG/charger-display/build/<mode>/`.

The script checksum-verifies TFT_eSPI **2.5.43**, fetched from the Arduino library
registry into ignored local storage, and uses it only to render an 80-line RGB565
RAM tile. Standard Arduino SPI sends pixels and reads touch: the graphics
library's own hardware path produced a white screen with this toolchain. No
third-party source archives, prebuilt images, local toolchains or backups are
committed. See [CHG bring-up notes](../BRINGUP.md)
for this temporary ESP32 build boundary. Bazel exposes `:sources`, and CI still
builds/tests the shared protocol and CHG host application.

On macOS the default Arduino data directory is `~/Library/Arduino15`; pass
`--arduino-data PATH` elsewhere. Dependency/cache directories and generated CLI
configuration are local to this board. The repo's path may contain spaces.

## Touch and demo

Hold BOOT for two seconds while the demo is running, release it and tap the four
corner targets. Calibration validates the fourth point and is saved in NVS;
BOOT cancels and each point has a timeout. Calibrate in demo only: live firmware
reuses saved calibration so it never pauses controller communications for touch
setup. Short BOOT presses cycle charging, ready, complete, fault, checks, no-link
and CV-taper scenes. The Control tab also advances scenarios in demo mode.

## UART and hardware

Dedicated UART2: **115200 8N1, RX GPIO32 / TX GPIO25**, repurposing the I2C header
(SDA32 / SCL25). STM32 TX goes to display RX and vice versa. USB UART0 remains
for programming. GPIO16/17 drive onboard LEDs and are not free UART pins.

The observed LCD/touch wiring matches LCDWIKI E32R40T: MOSI13, SCK14, MISO12,
CS15, DC2, shared EN reset, backlight27, touch CS33/IRQ36. User-reported glass
marking HSD-9190J-A3; MCU module ESP32-32E. Verify PCB/header labels before
wiring. The small onboard 3.7 V battery connector powers the display only; it
has no role in measuring or charging the vehicle accumulator.

UART wiring belongs inside the designed low-voltage domain. Use an appropriate
isolated interface if the STM32 and laptop/display grounds are separate domains.
Never tie USB/display ground to the HV pack for a reference, and never apply
5 V or RS-232 signals to ESP32 GPIO.

Primary references: [manufacturer pinout](https://www.lcdwiki.com/4.0inch_ESP32-32E_Display),
[panel initialization](https://www.lcdwiki.com/res/E32R40T/ST7796_Init.txt).
See the [hardware session](docs/HARDWARE_SESSION.md) and
[validation record](docs/VALIDATION.md). No valid original-firmware backup was
obtained in the initial session.

## Flashing and controller integration

Use the selected build's `flash_args` and the matching esptool/uploader, checking
port and chip identity first. The most recent port was `/dev/cu.usbserial-110`;
it can change on reconnection. Keep the NVS sector intact to retain calibration.
There is no automatic flash target in this initial package.

After building, upload with the same Arduino CLI configuration. From the repo
root (macOS/Linux shell), replacing the port with your display's current port:

```sh
arduino-cli upload \
  --config-file boards/CHG/charger-display/arduino-cli.yaml \
  --fqbn esp32:esp32:esp32:FlashSize=4M,FlashMode=dio,FlashFreq=40,UploadSpeed=115200,PartitionScheme=default \
  --port /dev/cu.usbserial-110 \
  --input-dir boards/CHG/charger-display/build/demo \
  boards/CHG/charger-display/firmware/ChargerDisplay
```

This uploads the demo bootloader, partition/boot-selection images and application
without erasing NVS calibration. Do not upload the merged 4 MB image if you want
to retain NVS. Close other serial monitors before upload. Automatic bootloader
entry normally needs no buttons. If it stalls at Connecting, hold BOOT, tap
RESET, and release BOOT once writing begins. Recalibrate with BOOT held for two
seconds while the demo is running only if navigation needs it.

Check runtime output after upload:

```sh
python3 boards/CHG/charger-display/tools/monitor.py --port /dev/cu.usbserial-110 --seconds 8
```

Expect `UI_ALIVE mode=DEMO`, increasing uptime, `link=1` for simulated data, and
`touch=1` when calibration is saved. This confirms firmware health, not a live
STM32 connection; check the screen and tabs physically as well.

START requires a 1.5-second hold, fresh telemetry, all required checks, no faults
and positive allowed power. STOP is a normal shutdown request and supersedes a
pending START. Neither replaces the physical emergency stop or independent
controller protection. SOC/SOE/ETA originate in the BMS/controller estimator;
completion is reported by the controller, not inferred from 100% SOC.

See the [protocol](../lib/PROTOCOL.md) and
[CHG setup](../README.md) before live integration.
