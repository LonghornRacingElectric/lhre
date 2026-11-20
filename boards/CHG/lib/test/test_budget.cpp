#include <assert.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include "ChargeBudget.h"

static chg_budget_inputs baseline() {
  chg_budget_inputs in = {240.0, 32.0,   0.99,   50.0,  0.95, 493.2,
                          20.0,  6600.0, 6600.0, 575.4, 1};
  return in;
}

static void near(double actual, double expected) {
  assert(fabs(actual - expected) <= 1e-8 * fmax(1.0, fabs(expected)));
}

static void invalid(const chg_budget_inputs* in) {
  const chg_budget_result out = chg_budget_calculate(in);
  assert(!out.valid && out.reason == CHG_BUDGET_INVALID);
  assert(out.allowed_dc_w == 0.0 && out.allowed_dc_current_a == 0.0);
}

static void knownScenarios() {
  chg_budget_inputs in = baseline();
  chg_budget_result out = chg_budget_calculate(&in);
  assert(out.valid);
  near(out.allowed_dc_w, 6600.0);
  near(out.allowed_dc_current_a, 13.381995133819951);
  in.allowed_ac_current_a = 12;
  in.ac_voltage_rms_v = 120;
  in.power_factor = 0.99;
  in.auxiliary_ac_w = 30;
  in.efficiency = 0.92;
  out = chg_budget_calculate(&in);
  assert(out.reason == CHG_BUDGET_AC_SUPPLY);
  near(out.allowed_dc_w, 1283.952);
  in = baseline();
  in.allowed_ac_current_a = 24;
  out = chg_budget_calculate(&in);
  near(out.allowed_dc_w, (240 * 24 * 0.99 - 50) * 0.95);
  assert(out.reason == CHG_BUDGET_AC_SUPPLY);
  in = baseline();
  in.bms_max_charge_current_a = 12;
  out = chg_budget_calculate(&in);
  near(out.allowed_dc_w, 5918.4);
  near(out.allowed_dc_current_a, 12);
  assert(out.reason == CHG_BUDGET_BMS);
  in.thermal_max_dc_w = 4100;
  out = chg_budget_calculate(&in);
  near(out.allowed_dc_w, 4100);
  assert(out.reason == CHG_BUDGET_THERMAL);
  in.profile_max_dc_w = 3000;
  out = chg_budget_calculate(&in);
  near(out.allowed_dc_w, 3000);
  assert(out.reason == CHG_BUDGET_PROFILE);
  in = baseline();
  in.pack_voltage_v = 575.4;
  out = chg_budget_calculate(&in);
  assert(out.valid && out.reason == CHG_BUDGET_VOLTAGE &&
         out.allowed_dc_w == 0);
  in.pack_voltage_v = 576;
  assert(chg_budget_calculate(&in).allowed_dc_w == 0);
  /* A different pack/profile uses its own ceiling and current. */
  in = baseline();
  in.profile_max_pack_v = 100;
  in.pack_voltage_v = 80;
  in.bms_max_charge_current_a = 10;
  out = chg_budget_calculate(&in);
  near(out.allowed_dc_w, 800);
  in.pack_voltage_v = 100;
  assert(chg_budget_calculate(&in).allowed_dc_w == 0);
}

static void validationAndZeroCaps() {
  invalid(0);
  chg_budget_inputs in = baseline();
  in.supply_current_confirmed = 0;
  invalid(&in);
  in.supply_current_confirmed = 2;
  invalid(&in);
  /* Enumerate every numeric field without relying on struct packing. */
  for (unsigned field = 0; field < 10; ++field) {
    for (unsigned bad = 0; bad < 4; ++bad) {
      in = baseline();
      double* values[] = {&in.ac_voltage_rms_v,
                          &in.allowed_ac_current_a,
                          &in.power_factor,
                          &in.auxiliary_ac_w,
                          &in.efficiency,
                          &in.pack_voltage_v,
                          &in.bms_max_charge_current_a,
                          &in.thermal_max_dc_w,
                          &in.profile_max_dc_w,
                          &in.profile_max_pack_v};
      const double badValues[] = {NAN, INFINITY, -INFINITY, -1.0};
      *values[field] = badValues[bad];
      invalid(&in);
    }
  }
  in = baseline();
  in.ac_voltage_rms_v = 0;
  invalid(&in);
  in = baseline();
  in.pack_voltage_v = 0;
  invalid(&in);
  in = baseline();
  in.profile_max_pack_v = 0;
  invalid(&in);
  in = baseline();
  in.power_factor = 0;
  invalid(&in);
  in = baseline();
  in.power_factor = 1.001;
  invalid(&in);
  in = baseline();
  in.efficiency = 0;
  invalid(&in);
  in = baseline();
  in.efficiency = 1.001;
  invalid(&in);
  in = baseline();
  in.allowed_ac_current_a = DBL_MAX;
  invalid(&in);
  in = baseline();
  in.bms_max_charge_current_a = DBL_MAX;
  invalid(&in);
  for (unsigned field = 0; field < 4; ++field) {
    in = baseline();
    double* caps[] = {&in.allowed_ac_current_a, &in.bms_max_charge_current_a,
                      &in.thermal_max_dc_w, &in.profile_max_dc_w};
    *caps[field] = 0;
    const chg_budget_result out = chg_budget_calculate(&in);
    assert(out.valid && out.allowed_dc_w == 0 && out.allowed_dc_current_a == 0);
  }
  in = baseline();
  in.auxiliary_ac_w = 9000;
  assert(chg_budget_calculate(&in).allowed_dc_w == 0);
}

static void budgetInvariants() {
  for (unsigned supply = 0; supply <= 40; ++supply) {
    for (unsigned amps = 0; amps <= 24; ++amps) {
      for (unsigned voltage = 100; voltage < 575; voltage += 19) {
        chg_budget_inputs in = baseline();
        in.allowed_ac_current_a = supply;
        in.bms_max_charge_current_a = amps;
        in.pack_voltage_v = voltage;
        const chg_budget_result out = chg_budget_calculate(&in);
        assert(out.valid && out.allowed_dc_w >= 0);
        assert(out.allowed_dc_w <= 6600);
        assert(out.allowed_dc_w <= out.supply_available_dc_w);
        assert(out.allowed_dc_w <= in.pack_voltage_v * amps);
        assert(out.allowed_dc_w <= in.thermal_max_dc_w);
        assert(out.allowed_dc_w <= in.profile_max_dc_w);
        assert(out.allowed_dc_current_a <= amps + 1e-10);
        in.allowed_ac_current_a += 1;
        assert(chg_budget_calculate(&in).allowed_dc_w >= out.allowed_dc_w);
      }
    }
  }
}

int main() {
  knownScenarios();
  validationAndZeroCaps();
  budgetInvariants();
  puts(
      "PASS: charge budget, supply profiles, BMS/thermal/profile/voltage caps, "
      "invalid inputs, zero caps, invariants");
}
