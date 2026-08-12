// Host simulation of the VCU: runs the app's real FreeRTOS tasks under the
// kernel's simulator port (real time), against LHAL host fakes, and prints
// what an observer on the CAN bus sees.
//
//   bazel run --config=local //boards/VCU:vcu_sim

#include <cstdio>

#include "FreeRTOS.h"
#include "lhal/host/can.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "lhre_can.hpp"
#include "task.h"
#include "vcu_app.hpp"

namespace {

constexpr uint32_t kSimDurationMs = 3000;

struct SimWorld {
  lhal::host::SystemClock clock;
  lhal::host::Gpio led;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can{&network};
  lhal::host::Can observer{&network};
};

// Watches the LED and the observer CAN node, prints changes, and ends the
// scheduler (which returns control to main) when the sim time is up.
void ObserverTaskLoop(void* arg) {
  SimWorld& world = *static_cast<SimWorld*>(arg);
  bool last_led = world.led.Read();
  uint32_t statuses_seen = 0;

  while (world.clock.Millis() < kSimDurationMs) {
    if (world.led.Read() != last_led) {
      last_led = world.led.Read();
      std::printf("[%4u ms] LED %s\n", world.clock.Millis(),
                  last_led ? "on" : "off");
    }

    lhal::CanFrame frame;
    while (world.observer.Receive(&frame)) {
      if (!lhre::can::VcuStatus::Matches(frame.id)) {
        continue;
      }
      auto status = lhre::can::VcuStatus::FromFrame(frame);
      ++statuses_seen;
      if (statuses_seen % 10 == 1) {
        std::printf("[%4u ms] CAN 0x%03X VcuStatus state=%s\n",
                    world.clock.Millis(), frame.id,
                    lhre::can::ToString(status.state));
      }
    }

    vTaskDelay(pdMS_TO_TICKS(1));
  }
  vTaskEndScheduler();
}

}  // namespace

int main() {
  static SimWorld world;

  vcu::Peripherals p;
  p.clock = &world.clock;
  p.status_led = &world.led;
  p.can = &world.vcu_can;
  static vcu::App app(p);

  std::printf("VCU sim: running for %u ms...\n", kSimDurationMs);
  app.StartTasks();

  static StaticTask_t observer_tcb;
  static StackType_t observer_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(ObserverTaskLoop, "observer", configMINIMAL_STACK_SIZE,
                    &world, tskIDLE_PRIORITY + 3, observer_stack,
                    &observer_tcb);

  vTaskStartScheduler();  // returns when the observer task ends the sim

  std::printf("VCU sim: done (%u statuses sent)\n", app.statuses_sent());
  return 0;
}
