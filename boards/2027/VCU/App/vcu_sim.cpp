// Host simulation of the VCU: runs the real application logic in real time
// against LHAL host fakes and prints what an observer on the CAN bus sees.
//
//   bazel run --config=local //boards/2027/VCU:vcu_sim

#include <cstdio>

#include "lhal/host/can.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "vcu_app.hpp"

int main() {
  lhal::host::SystemClock clock;
  lhal::host::Gpio led;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can(&network);
  lhal::host::Can observer(&network);

  vcu::Peripherals p;
  p.clock = &clock;
  p.status_led = &led;
  p.can = &vcu_can;
  vcu::App app(p);

  std::printf("VCU sim: running for 3 seconds...\n");
  bool last_led = led.Read();
  while (clock.Millis() < 3000) {
    app.Step();

    if (led.Read() != last_led) {
      last_led = led.Read();
      std::printf("[%4u ms] LED %s\n", clock.Millis(), last_led ? "on" : "off");
    }

    lhal::CanFrame frame;
    while (observer.Receive(&frame)) {
      uint32_t counter = static_cast<uint32_t>(frame.data[0]) |
                         (static_cast<uint32_t>(frame.data[1]) << 8) |
                         (static_cast<uint32_t>(frame.data[2]) << 16) |
                         (static_cast<uint32_t>(frame.data[3]) << 24);
      if (counter % 10 == 0) {
        std::printf("[%4u ms] CAN 0x%03X heartbeat #%u\n", clock.Millis(),
                    frame.id, counter);
      }
    }

    clock.DelayMs(1);
  }
  std::printf("VCU sim: done (%u heartbeats sent)\n", app.heartbeats_sent());
  return 0;
}
