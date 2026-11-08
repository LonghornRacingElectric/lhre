// Host simulation of the HVC: runs the app against LHAL host fakes and
// prints what an observer would see.
//
//   bazel run --config=local //boards/HVC:hvc_sim

#include <cstdio>

#include "FreeRTOS.h"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "task.h"
#include "hvc_app.hpp"

namespace {

constexpr uint32_t kSimDurationMs = 5000;  // one full rainbow cycle
constexpr uint32_t kPrintPeriodMs = 500;

struct SimWorld {
  lhal::host::SystemClock clock;
  lhal::host::Pwm red;
  lhal::host::Pwm green;
  lhal::host::Pwm blue;
};

// Samples the LED color, prints it, and ends the scheduler (which returns
// control to main) when the sim time is up.
void ObserverTaskLoop(void* arg) {
  SimWorld& world = *static_cast<SimWorld*>(arg);

  while (world.clock.Millis() < kSimDurationMs) {
    std::printf("[%4u ms] LED r=%.2f g=%.2f b=%.2f\n", world.clock.Millis(),
                world.red.duty(), world.green.duty(), world.blue.duty());
    vTaskDelay(pdMS_TO_TICKS(kPrintPeriodMs));
  }
  vTaskEndScheduler();
}

}  // namespace

int main() {
  static SimWorld world;

  hvc::Peripherals p;
  p.clock = &world.clock;
  p.led_red = &world.red;
  p.led_green = &world.green;
  p.led_blue = &world.blue;
  static hvc::App app(p);

  std::printf("HVC sim: running for %u ms...\n", kSimDurationMs);
  app.StartTasks();

  static StaticTask_t observer_tcb;
  static StackType_t observer_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(ObserverTaskLoop, "observer", configMINIMAL_STACK_SIZE,
                    &world, tskIDLE_PRIORITY + 2, observer_stack,
                    &observer_tcb);

  vTaskStartScheduler();  // returns when the observer task ends the sim

  std::printf("HVC sim: done (%u LED updates)\n", app.led_updates());
  return 0;
}
