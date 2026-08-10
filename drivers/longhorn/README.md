# Longhorn library

Shared board services. Everything here is
written against [LHAL](../lhal/README.md) interfaces without ST headers
so each module builds unchanged for the MCU and the host.

Each module is its own target so boards pull in only what they use:

- `//drivers/longhorn:led` — `longhorn::RgbLed`, the RGB status LED
  (boot-white, `Set`, rainbow animation, `Disable` for error latching).
- `//drivers/longhorn:console` — `longhorn::Console`, printf-style line
  output over any `lhal::Uart` stream (debug UART or USB VCP via
  `lhal::stm32::UsbCdc`). Not thread-safe on its own; serialize behind a
  mutex or logger task when printing from multiple RTOS tasks.

## Using a module on a board

The board owns peripheral bring-up, then hands LHAL backend instances to the
driver at construction (same pattern as `boards/VCU`'s `Peripherals`):

```cpp
static lhal::stm32::Pwm led_r(&htim2, TIM_CHANNEL_1);
static lhal::stm32::Pwm led_g(&htim2, TIM_CHANNEL_2);
static lhal::stm32::Pwm led_b(&htim2, TIM_CHANNEL_3);
led_r.Start();
led_g.Start();
led_b.Start();
static longhorn::RgbLed led(&led_r, &led_g, &led_b);
```

In tests, construct the same driver with `lhal::host::*` fakes instead.

Task/RTOS wiring stays in the board app (see `boards/VCU/App`): drive
`RgbLed::Rainbow(dt)` from a periodic task or a `Step()` loop. Pure,
LHAL-free logic (CRC, pack/unpack, ring buffers) belongs in `lib/`, not here.
