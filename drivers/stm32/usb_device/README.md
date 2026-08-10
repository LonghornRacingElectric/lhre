# ST USB Device middleware

ST's `STM32_USB_Device_Library` (Core + the CDC "virtual COM port" class),
pinned from the standalone
[stm32-mw-usb-device](https://github.com/STMicroelectronics/stm32-mw-usb-device)
repo via `deps.bzl` — the same recipe as the HAL families, but pinned once:
the middleware is family-independent, sitting on each board's generated
`usbd_conf.c` glue.

| Target     | Contents                                              |
| ---------- | ----------------------------------------------------- |
| `:headers` | USB Device core + CDC class headers.                  |
| `:srcs`    | Core + CDC `.c` sources (templates excluded), compiled inside each firmware binary — they include the board's `usbd_conf.h`. |

Boards never reference these targets directly: set `enable_usb = True` on
the board's `firmware_project` and the macro wires them in together with
the board's CubeMX-generated `USB_Device/` files. The generated
`usbd_cdc_if.c` is deliberately **not** compiled — its only content is the
CDC callback struct (`USBD_Interface_fops_FS`), which
`lhal/stm32/usb_cdc.cpp` defines instead so traffic routes into
`lhal::stm32::UsbCdc` (an `lhal::Uart`). See the USB note in
[drivers/lhal](../../lhal/README.md) and the options list in
[tools/firmware](../../../tools/firmware/README.md).

Only the CDC class is exposed today; add globs in `usb_device.BUILD` for
other classes (MSC, HID, DFU, ...) if a board ever needs them.
