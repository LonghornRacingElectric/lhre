#include "pedals.hpp"

#include <cmath>

#include "lookup.hpp"

namespace vcu {
namespace {

float Deadzone(float x, float lo, float hi) {
  if (x <= lo) {
    return 0.0f;
  }
  if (x >= hi) {
    return 1.0f;
  }
  return (x - lo) / (hi - lo);
}

}  // namespace

PedalOutputs PedalProcessor::Update(const PedalInputs& in, uint32_t now_ms,
                                    const AppsParams& apps,
                                    const BseParams& bse) {
  PedalOutputs out;

  // APPS
  out.apps1_travel_unclamped =
      Normalize(in.apps1_v, apps.apps1_v_at_0, apps.apps1_v_at_100);
  out.apps2_travel_unclamped =
      Normalize(in.apps2_v, apps.apps2_v_at_0, apps.apps2_v_at_100);
  out.apps1_travel = Clamp(out.apps1_travel_unclamped, 0.0f, 1.0f);
  out.apps2_travel = Clamp(out.apps2_travel_unclamped, 0.0f, 1.0f);

  // Open/short window checks on each sensor (T.4.2, TODO(verify rule)).
  out.apps1_disconnect = in.apps1_v < apps.window_low_v;
  out.apps2_disconnect = in.apps2_v < apps.window_low_v;
  out.apps1_out_of_range = in.apps1_v > apps.window_high_v;
  out.apps2_out_of_range = in.apps2_v > apps.window_high_v;
  const bool apps_range = out.apps1_disconnect || out.apps2_disconnect ||
                          out.apps1_out_of_range || out.apps2_out_of_range;

  // T.4.2: deviation over 10 points, or a sensor out of range, for more
  // than 100 ms is an implausibility (TODO(verify rule) text).
  out.apps_mismatch =
      std::fabs(out.apps1_travel - out.apps2_travel) > apps.max_deviation;
  out.apps_implausible = apps_implausible_timer_.Update(
      out.apps_mismatch || apps_range, now_ms, apps.implausible_ms);

  const float average = 0.5f * (out.apps1_travel + out.apps2_travel);
  out.pedal_travel =
      Deadzone(pedal_filter_.Update(average, apps.ema_alpha),
               apps.min_travel_deadzone, apps.max_travel_deadzone);

  // BSE
  out.bse1_psi_unclamped =
      Normalize(in.bse1_v, bse.v_at_0_psi, bse.v_at_full) * bse.full_scale_psi;
  out.bse2_psi_unclamped =
      Normalize(in.bse2_v, bse.v_at_0_psi, bse.v_at_full) * bse.full_scale_psi;
  out.bse1_psi = Clamp(out.bse1_psi_unclamped, 0.0f, bse.full_scale_psi);
  out.bse2_psi = Clamp(out.bse2_psi_unclamped, 0.0f, bse.full_scale_psi);

  // Open/short window checks (TODO(verify rule) T.4.3 / EV brake sensor).
  out.bse1_disconnect = in.bse1_v < bse.window_low_v;
  out.bse2_disconnect = in.bse2_v < bse.window_low_v;
  out.bse1_out_of_range = in.bse1_v > bse.window_high_v;
  out.bse2_out_of_range = in.bse2_v > bse.window_high_v;
  out.bse1_fault = bse1_fault_timer_.Update(
      out.bse1_disconnect || out.bse1_out_of_range, now_ms, bse.range_fault_ms);
  out.bse2_fault = bse2_fault_timer_.Update(
      out.bse2_disconnect || out.bse2_out_of_range, now_ms, bse.range_fault_ms);

  out.bse_psi_filtered =
      bse_filter_.Update(0.5f * (out.bse1_psi + out.bse2_psi), bse.ema_alpha);

  // lhre-2026 BSE.c:29-39: hysteresis on the filtered pressure.
  if (out.bse_psi_filtered >= bse.pressed_on_psi) {
    brake_pressed_ = true;
  } else if (out.bse_psi_filtered < bse.pressed_off_psi) {
    brake_pressed_ = false;
  }
  out.brake_pressed = brake_pressed_;
  out.brake_for_rtd = out.bse_psi_filtered >= bse.brake_light_psi;
  out.brake_light = out.brake_for_rtd ? bse.brake_light_duty : 0.0f;

  // EV.4.7 thresholds refer to calibrated mechanical travel, before the
  // torque-demand filter/deadzone can shift or delay the trip and release.
  if (brake_pressed_ && average > bse.bot_pedal_on) {
    bot_latched_ = true;
  } else if (average < bse.bot_pedal_off) {
    bot_latched_ = false;
  }
  out.brake_over_throttle = bot_latched_;

  // Firmware's own hard-brake view against the BSPD comparators. A
  // diagnostic only: the BSPD acts on its own.
  out.firmware_hard_brake = in.bse1_pin_v > bse.hard_brake_pin_v &&
                            in.bse2_pin_v > bse.hard_brake_pin_v;
  out.hard_brake_mismatch = hard_brake_mismatch_timer_.Update(
      out.firmware_hard_brake != in.hard_braking_hw, now_ms,
      bse.hard_brake_mismatch_ms);

  return out;
}

}  // namespace vcu
