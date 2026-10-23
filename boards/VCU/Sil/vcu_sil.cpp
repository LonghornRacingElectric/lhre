// Software-in-the-loop shim: the unmodified vcu::App on LHAL host fakes and
// a virtual clock, behind the C ABI in vcu_sil.h. The App is driven the way
// App/vcu_app_test.cpp drives it: the pack state goes in as the CAN frames
// the HVC sends. The accelerator and motor speed have no CAN or LHAL path
// into the App yet, so they go in through App::SetDriveInputs, and the
// command comes out of App::torque_command_nm().

#include "vcu_sil.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iterator>
#include <limits>
#include <new>

#include "lhal/host/can.hpp"
#include "lhal/host/system.hpp"
#include "lhre_can_hvc.hpp"
#include "vcu_app.hpp"

namespace {

using lhre::can::hvc::HvcPackStatus;

constexpr uint32_t kPackStatusPeriodMs =
    static_cast<uint32_t>(1000.0f / HvcPackStatus::kFrequencyHz);

vcu::Peripherals MakePeripherals(lhal::Clock* clock, lhal::CanBus* can) {
  vcu::Peripherals peripherals;
  peripherals.clock = clock;
  peripherals.can = can;
  return peripherals;
}

// Saturates to what the fields can encode: a float-to-int cast outside the
// raw type's range is undefined. The bounds go through the generated getters
// so the scale lives only in lib/spec.
HvcPackStatus PackStatusFrom(double pack_voltage_v, double pack_current_a) {
  HvcPackStatus lowest;
  lowest.pack_voltage_raw =
      std::numeric_limits<decltype(lowest.pack_voltage_raw)>::min();
  lowest.pack_current_raw =
      std::numeric_limits<decltype(lowest.pack_current_raw)>::min();
  HvcPackStatus highest;
  highest.pack_voltage_raw =
      std::numeric_limits<decltype(highest.pack_voltage_raw)>::max();
  highest.pack_current_raw =
      std::numeric_limits<decltype(highest.pack_current_raw)>::max();

  HvcPackStatus pack;
  pack.set_pack_voltage(std::clamp(static_cast<float>(pack_voltage_v),
                                   lowest.pack_voltage(),
                                   highest.pack_voltage()));
  pack.set_pack_current(std::clamp(static_cast<float>(pack_current_a),
                                   lowest.pack_current(),
                                   highest.pack_current()));
  return pack;
}

struct Sil {
  explicit Sil(bool traction_control)
      : app(MakePeripherals(&clock, &vcu_can), traction_control) {}

  lhal::host::TestClock clock;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can{&network};
  // Stands in for the HVC, which reports the pack.
  lhal::host::Can hvc_can{&network};
  vcu::App app;

  bool pack_status_sent = false;
  uint32_t last_pack_status_ms = 0;
};

}  // namespace

void* vcu_sil_create(int traction_control) {
  return new (std::nothrow) Sil(traction_control != 0);
}

void vcu_sil_destroy(void* handle) { delete static_cast<Sil*>(handle); }

int vcu_sil_step(void* handle, double time_s, double accel_pedal,
                 double brake_pedal, double motor_speed_rad_s,
                 double pack_voltage_v, double pack_current_a,
                 double* motor_torque_nm, double* regen_limit_nm) {
  if (handle == nullptr || motor_torque_nm == nullptr ||
      regen_limit_nm == nullptr) {
    return VCU_SIL_ERR_NULL_ARGUMENT;
  }
  // The brake is checked but reaches nothing yet: the App has no brake
  // input (Sil/README.md).
  const double inputs[] = {time_s,         accel_pedal,
                           brake_pedal,    motor_speed_rad_s,
                           pack_voltage_v, pack_current_a};
  if (!std::all_of(std::begin(inputs), std::end(inputs),
                   [](double input) { return std::isfinite(input); })) {
    return VCU_SIL_ERR_NON_FINITE_INPUT;
  }

  auto* sil = static_cast<Sil*>(handle);
  const long long target_ms = std::llround(time_s * 1000.0);
  if (target_ms < static_cast<long long>(sil->clock.Millis())) {
    return VCU_SIL_ERR_TIME_WENT_BACKWARDS;
  }
  sil->clock.Advance(static_cast<uint32_t>(target_ms - sil->clock.Millis()));

  const uint32_t now_ms = sil->clock.Millis();
  if (!sil->pack_status_sent ||
      lhal::ElapsedMs(now_ms, sil->last_pack_status_ms, kPackStatusPeriodMs)) {
    sil->hvc_can.Send(PackStatusFrom(pack_voltage_v, pack_current_a).ToFrame());
    sil->pack_status_sent = true;
    sil->last_pack_status_ms = now_ms;
  }

  sil->app.SetDriveInputs(static_cast<float>(accel_pedal),
                          static_cast<float>(motor_speed_rad_s));
  sil->app.Step();

  // The stand-in HVC ignores the VcuStatus frames it hears; drain them so
  // its queue does not grow over a long run.
  lhal::CanFrame frame;
  while (sil->hvc_can.Receive(&frame)) {
  }
  // The fakes keep a log of every frame sent; nothing here reads it.
  sil->vcu_can.ClearSent();
  sil->hvc_can.ClearSent();

  *motor_torque_nm = sil->app.torque_command_nm();
  // lib/spec has no regen command yet, so the App allows none.
  *regen_limit_nm = 0.0;
  return VCU_SIL_OK;
}
