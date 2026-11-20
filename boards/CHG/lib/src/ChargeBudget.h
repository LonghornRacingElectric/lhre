#ifndef CHARGE_BUDGET_H
#define CHARGE_BUDGET_H

/* Portable C99/C++11 helper. A numerical budget is not charge authorization.
 * Call in the STM32 control loop using fresh, validated controller inputs.
 * Do not compile with -ffast-math: non-finite inputs must fail closed. */
#include <math.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CHG_BUDGET_CHARGER_MAX_W 6600.0

typedef enum {
  CHG_BUDGET_INVALID = 0,
  CHG_BUDGET_AC_SUPPLY = 2,
  CHG_BUDGET_CHARGER = 3,
  CHG_BUDGET_BMS = 4,
  CHG_BUDGET_THERMAL = 5,
  CHG_BUDGET_VOLTAGE = 6,
  CHG_BUDGET_PROFILE = 7
} chg_budget_reason;

typedef struct {
  double ac_voltage_rms_v;
  double allowed_ac_current_a; /* Explicit controller/operator input. */
  double power_factor;         /* (0, 1]; conservative validated value. */
  double auxiliary_ac_w;       /* Loads included in the same supply budget. */
  double efficiency;           /* AC-to-DC efficiency, (0, 1]. */
  double pack_voltage_v;
  double bms_max_charge_current_a;
  double thermal_max_dc_w;
  double profile_max_dc_w;
  double profile_max_pack_v; /* Approved operational ceiling, not a target. */
  uint8_t supply_current_confirmed; /* Exactly 1; no outlet auto-detection. */
} chg_budget_inputs;

typedef struct {
  double allowed_dc_w;
  double allowed_dc_current_a;
  double supply_available_dc_w;
  uint8_t valid;
  chg_budget_reason reason;
} chg_budget_result;

static inline int chg_budget_finite_nonnegative(double value) {
  return isfinite(value) && value >= 0.0;
}

static inline chg_budget_result chg_budget_calculate(
    const chg_budget_inputs* in) {
  chg_budget_result out = {0.0, 0.0, 0.0, 0, CHG_BUDGET_INVALID};
  double available_ac_w, bms_dc_w;
  if (!in || in->supply_current_confirmed != 1 ||
      !chg_budget_finite_nonnegative(in->ac_voltage_rms_v) ||
      in->ac_voltage_rms_v == 0.0 ||
      !chg_budget_finite_nonnegative(in->allowed_ac_current_a) ||
      !isfinite(in->power_factor) || in->power_factor <= 0.0 ||
      in->power_factor > 1.0 ||
      !chg_budget_finite_nonnegative(in->auxiliary_ac_w) ||
      !isfinite(in->efficiency) || in->efficiency <= 0.0 ||
      in->efficiency > 1.0 ||
      !chg_budget_finite_nonnegative(in->pack_voltage_v) ||
      in->pack_voltage_v == 0.0 ||
      !chg_budget_finite_nonnegative(in->bms_max_charge_current_a) ||
      !chg_budget_finite_nonnegative(in->thermal_max_dc_w) ||
      !chg_budget_finite_nonnegative(in->profile_max_dc_w) ||
      !chg_budget_finite_nonnegative(in->profile_max_pack_v) ||
      in->profile_max_pack_v == 0.0) {
    return out;
  }
  available_ac_w =
      in->ac_voltage_rms_v * in->allowed_ac_current_a * in->power_factor;
  bms_dc_w = in->pack_voltage_v * in->bms_max_charge_current_a;
  if (!isfinite(available_ac_w) || !isfinite(bms_dc_w)) return out;
  available_ac_w -= in->auxiliary_ac_w;
  if (available_ac_w < 0.0) available_ac_w = 0.0;
  out.supply_available_dc_w = available_ac_w * in->efficiency;
  if (!isfinite(out.supply_available_dc_w)) return out;
  out.valid = 1;
  if (in->pack_voltage_v >= in->profile_max_pack_v) {
    out.reason = CHG_BUDGET_VOLTAGE;
    return out;
  }
  out.allowed_dc_w = CHG_BUDGET_CHARGER_MAX_W;
  out.reason = CHG_BUDGET_CHARGER;
  /* <= makes simultaneous constraints deterministic: profile, thermal, BMS,
   * supply, charger in decreasing precedence. */
  if (out.supply_available_dc_w <= out.allowed_dc_w) {
    out.allowed_dc_w = out.supply_available_dc_w;
    out.reason = CHG_BUDGET_AC_SUPPLY;
  }
  if (bms_dc_w <= out.allowed_dc_w) {
    out.allowed_dc_w = bms_dc_w;
    out.reason = CHG_BUDGET_BMS;
  }
  if (in->thermal_max_dc_w <= out.allowed_dc_w) {
    out.allowed_dc_w = in->thermal_max_dc_w;
    out.reason = CHG_BUDGET_THERMAL;
  }
  if (in->profile_max_dc_w <= out.allowed_dc_w) {
    out.allowed_dc_w = in->profile_max_dc_w;
    out.reason = CHG_BUDGET_PROFILE;
  }
  out.allowed_dc_current_a = out.allowed_dc_w / in->pack_voltage_v;
  if (!isfinite(out.allowed_dc_current_a)) {
    chg_budget_result invalid = {0.0, 0.0, 0.0, 0, CHG_BUDGET_INVALID};
    return invalid;
  }
  return out;
}

#ifdef __cplusplus
}
#endif
#endif
