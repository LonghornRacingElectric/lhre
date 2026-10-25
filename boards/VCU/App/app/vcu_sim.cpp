// Host simulation of the VCU: the app's real FreeRTOS tasks under the
// kernel's simulator port (real time), against LHAL host fakes, with a
// scripted car driving it through precharge, RTD, a pedal press, and a
// shutdown-loop opening. Prints state changes and the inverter command.
//
//   bazel run --config=local //boards/VCU:vcu_sim

#include <cstdio>

#include "FreeRTOS.h"
#include "app/vcu_app.hpp"
#include "task.h"
#include "testing/bench.hpp"

namespace {

constexpr uint32_t kSimDurationMs = 4000;

// The bench's TestClock, advanced by a 1 ms task so the app sees sim time.
vcu::bench::Bench* g_bench = nullptr;
vcu::App* g_app = nullptr;

void CarTaskLoop(void*) {
  vcu::bench::Bench& b = *g_bench;
  lhre::can::VcuState last = lhre::can::VcuState::kInit;
  for (uint32_t t = 0; t < kSimDurationMs; ++t) {
    if (t == 200) b.hvc_state = lhre::can::HvcState::kPrecharging;
    if (t == 700) b.Energize();
    if (t == 900) b.SetBrakePsi(500.0f);
    if (t == 1100) b.PressRtd(true);
    if (t == 1300) b.SetBrakePsi(0.0f);
    if (t == 1500) b.SetPedalTravel(0.5f);
    if (t == 3000) b.SetLoop(24.0f, 0.0f);
    if (t % 5 == 0) b.SendCarFrames();
    b.clock.Advance(1);
    const lhre::can::VcuState s = g_app->state();
    if (s != last || t % 500 == 0) {
      std::printf("[%4u ms] state=%-12s torque=%6.1f Nm faults=0x%05x\n", t,
                  lhre::can::ToString(s), g_app->torque_command(),
                  static_cast<unsigned>(g_app->faults()));
      last = s;
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  vTaskEndScheduler();
}

}  // namespace

int main() {
  static vcu::bench::Bench bench;
  g_bench = &bench;
  static vcu::App app(bench.peripherals());
  g_app = &app;

  std::printf("VCU sim: running for %u ms...\n", kSimDurationMs);
  app.StartTasks();

  static StaticTask_t car_tcb;
  static StackType_t car_stack[4 * configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(CarTaskLoop, "car", 4 * configMINIMAL_STACK_SIZE, nullptr,
                    tskIDLE_PRIORITY + 7, car_stack, &car_tcb);

  vTaskStartScheduler();
  std::printf("VCU sim: done in state %s\n", lhre::can::ToString(app.state()));
  return 0;
}
