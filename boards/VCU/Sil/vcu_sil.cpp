#include "Sil/vcu_sil.h"

#include <cmath>
#include <cstddef>
#include <cstring>
#include <limits>
#include <new>
#include <string>

#include "Sil/rig.hpp"
#include "app/vcu_app.hpp"
#include "lhre/build_info.hpp"
#include "lhre_can_types.hpp"
#include "safety/state_machine.hpp"
#include "test/bench.hpp"

struct vcu_sil_handle {
  explicit vcu_sil_handle(lhre::can::EventMode mode) : rig(mode) {}

  vcu::sil::Rig rig;
  vcu::ControlSnapshot snapshot;  // from the last advance
  std::string console;  // taken from the rig, not yet read by the caller
  bool broken = false;  // an internal error left the rig half-advanced
};

namespace {

using vcu::sil::RigInputs;
using vcu::sil::RigOutputs;

constexpr uint32_t kMaxAdvanceMs = 1000;
// The shutdown sense taps saturate the ADC at about 27.4 V of loop.
constexpr double kMaxLoopV = 27.0;
constexpr int32_t kMaxHvcState =
    static_cast<int32_t>(lhre::can::HvcState::kCharging);
constexpr int32_t kMaxEventMode =
    static_cast<int32_t>(lhre::can::EventMode::kEndurance);

// Converting a finite double beyond float range is undefined behaviour.
float ToFloat(double v) {
  constexpr double kMax = std::numeric_limits<float>::max();
  return static_cast<float>(v > kMax ? kMax : (v < -kMax ? -kMax : v));
}

bool InRange(double v, double lo, double hi) { return v >= lo && v <= hi; }

int32_t CheckInputs(const vcu_sil_inputs& in, const vcu::Params& p) {
  const double values[] = {
      in.accelerator_travel,   in.brake_front_psi,      in.brake_rear_psi,
      in.steering_angle_deg,   in.shutdown_loop_v,      in.wheel_speed_rad_s[0],
      in.wheel_speed_rad_s[1], in.wheel_speed_rad_s[2], in.wheel_speed_rad_s[3],
      in.motor_speed_rad_s,    in.motor_torque_nm,      in.dc_bus_voltage_v,
      in.dc_bus_current_a,     in.min_cell_v,           in.max_cell_v,
  };
  for (const double v : values) {
    if (!std::isfinite(v)) {
      return VCU_SIL_ERR_NON_FINITE;
    }
  }
  const double full_scale = p.bse.full_scale_psi;
  if (!InRange(in.accelerator_travel, 0.0, 1.0) ||
      !InRange(in.brake_front_psi, 0.0, full_scale) ||
      !InRange(in.brake_rear_psi, 0.0, full_scale) ||
      !InRange(in.shutdown_loop_v, 0.0, kMaxLoopV) || in.hvc_state < 0 ||
      in.hvc_state > kMaxHvcState) {
    return VCU_SIL_ERR_RANGE;
  }
  return VCU_SIL_OK;
}

RigInputs ToRig(const vcu_sil_inputs& in) {
  RigInputs r;
  r.accelerator_travel = ToFloat(in.accelerator_travel);
  r.brake_front_psi = ToFloat(in.brake_front_psi);
  r.brake_rear_psi = ToFloat(in.brake_rear_psi);
  r.steering_angle_deg = ToFloat(in.steering_angle_deg);
  r.rtd_pressed = in.rtd_pressed != 0;
  r.hvc_state = static_cast<lhre::can::HvcState>(in.hvc_state);
  r.contactors_closed = in.contactors_closed != 0;
  r.shutdown_loop_v = ToFloat(in.shutdown_loop_v);
  for (int i = 0; i < 4; ++i) {
    r.wheel_speed_rad_s[i] = ToFloat(in.wheel_speed_rad_s[i]);
  }
  r.motor_speed_rad_s = ToFloat(in.motor_speed_rad_s);
  r.motor_torque_nm = ToFloat(in.motor_torque_nm);
  r.dc_bus_voltage_v = ToFloat(in.dc_bus_voltage_v);
  r.dc_bus_current_a = ToFloat(in.dc_bus_current_a);
  r.min_cell_v = ToFloat(in.min_cell_v);
  r.max_cell_v = ToFloat(in.max_cell_v);
  return r;
}

// FromRig() passes the rig's saturation bits through as they are.
constexpr bool SameBit(uint32_t rig, int abi) {
  return rig == static_cast<uint32_t>(abi);
}
static_assert(SameBit(vcu::sil::kSatWheelSpeed, VCU_SIL_SAT_WHEEL_SPEED));
static_assert(SameBit(vcu::sil::kSatMotorSpeed, VCU_SIL_SAT_MOTOR_SPEED));
static_assert(SameBit(vcu::sil::kSatMotorTorque, VCU_SIL_SAT_MOTOR_TORQUE));
static_assert(SameBit(vcu::sil::kSatBusVoltage, VCU_SIL_SAT_BUS_VOLTAGE));
static_assert(SameBit(vcu::sil::kSatBusCurrent, VCU_SIL_SAT_BUS_CURRENT));
static_assert(SameBit(vcu::sil::kSatCellVoltage, VCU_SIL_SAT_CELL_VOLTAGE));
static_assert(SameBit(vcu::sil::kSatSteering, VCU_SIL_SAT_STEERING));

void FromRig(const RigOutputs& r, uint64_t frames_dropped,
             vcu_sil_outputs* out) {
  const vcu::ControlSnapshot& s = r.snapshot;
  out->now_ms = r.now_ms;
  out->control_ms = s.now_ms;
  out->torque_frames = r.torque_frames;
  out->frames_dropped = frames_dropped > UINT32_MAX
                            ? UINT32_MAX
                            : static_cast<uint32_t>(frames_dropped);
  out->inverter_enable = r.command.enable ? 1 : 0;
  out->direction = r.command.direction ? 1 : 0;
  out->torque_request_nm = r.command.torque_request_nm;
  out->torque_limit_nm = r.command.torque_limit_nm;
  out->saturated = r.saturated;
  out->state = static_cast<int32_t>(s.state);
  out->faults = s.faults;
  out->primary_fault = static_cast<int32_t>(s.primary_fault);
  out->event_mode = static_cast<int32_t>(s.event_mode);
  out->loop_state = static_cast<int32_t>(s.loop);
  out->motor_speed_valid = s.motor_speed_valid ? 1 : 0;
  out->adc_fresh = s.adc_fresh ? 1 : 0;
  out->wheel_control_valid = s.wheel_control.valid ? 1 : 0;
  out->buzzer = s.buzzer ? 1 : 0;
  out->hard_braking = s.inputs.hard_braking ? 1 : 0;
  out->pedal_travel = s.pedals.pedal_travel;
  out->apps1_travel = s.pedals.apps1_travel;
  out->apps2_travel = s.pedals.apps2_travel;
  out->bse1_psi = s.pedals.bse1_psi;
  out->bse2_psi = s.pedals.bse2_psi;
  out->bse_filtered_psi = s.pedals.bse_psi_filtered;
  out->bse3_psi = s.bse3_psi;
  out->steering_angle_deg = s.steering.angle_deg;
  out->motor_rpm = s.motor_rpm;
  out->torque_lookup_nm = s.torque.lookup;
  out->torque_derated_nm = s.torque.derated;
  out->torque_power_limited_nm = s.torque.power_limited;
  out->torque_traction_nm = s.torque.traction;
  out->torque_command_nm = s.torque_command;
  out->power_w = s.torque.power_w;
  out->power_trim_w = s.torque.power_trim_w;
  out->soe_pct = s.battery.soe_pct;
  out->wheel_reference_m_s = s.wheel_control.reference_m_s;
  out->wheel_drive_slip = s.wheel_control.drive_slip;
}

// The first row of the state machine's table whose reason reads `reason`,
// -1 for the boot reason. Rows worded alike share an id: the from-state that
// would tell them apart is not known after an advance of several ticks.
int32_t TransitionId(const char* reason) {
  if (reason == nullptr) {
    return -2;
  }
  if (std::strcmp(reason, "boot") == 0) {
    return -1;
  }
  const vcu::Transition* table = vcu::StateMachine::table();
  for (size_t i = 0; i < vcu::StateMachine::table_size(); ++i) {
    if (std::strcmp(table[i].reason, reason) == 0) {
      return static_cast<int32_t>(i);
    }
  }
  return -2;
}

void FromSnapshot(const vcu::ControlSnapshot& s, vcu_sil_snapshot* out) {
  out->now_ms = s.now_ms;
  out->state = static_cast<int32_t>(s.state);
  out->last_transition_id = TransitionId(s.last_transition);
  out->faults = s.faults;
  out->primary_fault = static_cast<int32_t>(s.primary_fault);
  out->event_mode = static_cast<int32_t>(s.event_mode);
  out->loop_state = static_cast<int32_t>(s.loop);
  out->motor_speed_valid = s.motor_speed_valid ? 1 : 0;
  out->adc_fresh = s.adc_fresh ? 1 : 0;
  out->inverter_enable = s.inverter_enable ? 1 : 0;
  out->buzzer = s.buzzer ? 1 : 0;
  out->linelock_request = s.linelock_request ? 1 : 0;
  out->brake_flags_latched = s.brake_flags_latched ? 1 : 0;
  out->fault_resets = static_cast<int32_t>(s.fault_resets);
  out->bse3_available = s.bse3_available ? 1 : 0;
  out->launch_armed = s.launch_armed ? 1 : 0;
  out->regen_current_cut = s.regen_current_cut ? 1 : 0;
  out->motor_rpm = s.motor_rpm;
  out->torque_command_nm = s.torque_command;
  out->brake_bias = s.brake_bias;
  out->bse3_psi = s.bse3_psi;
  out->analog_apps1_counts = static_cast<int32_t>(s.analog.apps1.counts);
  out->analog_apps2_counts = static_cast<int32_t>(s.analog.apps2.counts);
  out->analog_bse1_counts = static_cast<int32_t>(s.analog.bse1.counts);
  out->analog_bse2_counts = static_cast<int32_t>(s.analog.bse2.counts);
  out->analog_steering_counts = static_cast<int32_t>(s.analog.steering.counts);
  out->analog_shutdown_in_counts =
      static_cast<int32_t>(s.analog.shutdown_in.counts);
  out->analog_shutdown_out_counts =
      static_cast<int32_t>(s.analog.shutdown_out.counts);
  out->analog_vrefint_counts = static_cast<int32_t>(s.analog.vrefint_counts);
  out->analog_vdda_from_vrefint = s.analog.vdda_from_vrefint ? 1 : 0;
  out->analog_apps1_pin_v = s.analog.apps1.pin_v;
  out->analog_apps1_source_v = s.analog.apps1.source_v;
  out->analog_apps2_pin_v = s.analog.apps2.pin_v;
  out->analog_apps2_source_v = s.analog.apps2.source_v;
  out->analog_bse1_pin_v = s.analog.bse1.pin_v;
  out->analog_bse1_source_v = s.analog.bse1.source_v;
  out->analog_bse2_pin_v = s.analog.bse2.pin_v;
  out->analog_bse2_source_v = s.analog.bse2.source_v;
  out->analog_steering_pin_v = s.analog.steering.pin_v;
  out->analog_steering_source_v = s.analog.steering.source_v;
  out->analog_shutdown_in_pin_v = s.analog.shutdown_in.pin_v;
  out->analog_shutdown_in_source_v = s.analog.shutdown_in.source_v;
  out->analog_shutdown_out_pin_v = s.analog.shutdown_out.pin_v;
  out->analog_shutdown_out_source_v = s.analog.shutdown_out.source_v;
  out->analog_vdda = s.analog.vdda;
  out->pedals_apps1_disconnect = s.pedals.apps1_disconnect ? 1 : 0;
  out->pedals_apps2_disconnect = s.pedals.apps2_disconnect ? 1 : 0;
  out->pedals_apps1_out_of_range = s.pedals.apps1_out_of_range ? 1 : 0;
  out->pedals_apps2_out_of_range = s.pedals.apps2_out_of_range ? 1 : 0;
  out->pedals_apps_mismatch = s.pedals.apps_mismatch ? 1 : 0;
  out->pedals_apps_implausible = s.pedals.apps_implausible ? 1 : 0;
  out->pedals_bse1_disconnect = s.pedals.bse1_disconnect ? 1 : 0;
  out->pedals_bse2_disconnect = s.pedals.bse2_disconnect ? 1 : 0;
  out->pedals_bse1_out_of_range = s.pedals.bse1_out_of_range ? 1 : 0;
  out->pedals_bse2_out_of_range = s.pedals.bse2_out_of_range ? 1 : 0;
  out->pedals_bse1_fault = s.pedals.bse1_fault ? 1 : 0;
  out->pedals_bse2_fault = s.pedals.bse2_fault ? 1 : 0;
  out->pedals_brake_pressed = s.pedals.brake_pressed ? 1 : 0;
  out->pedals_brake_for_rtd = s.pedals.brake_for_rtd ? 1 : 0;
  out->pedals_brake_over_throttle = s.pedals.brake_over_throttle ? 1 : 0;
  out->pedals_firmware_hard_brake = s.pedals.firmware_hard_brake ? 1 : 0;
  out->pedals_hard_brake_mismatch = s.pedals.hard_brake_mismatch ? 1 : 0;
  out->pedals_apps1_travel_unclamped = s.pedals.apps1_travel_unclamped;
  out->pedals_apps2_travel_unclamped = s.pedals.apps2_travel_unclamped;
  out->pedals_apps1_travel = s.pedals.apps1_travel;
  out->pedals_apps2_travel = s.pedals.apps2_travel;
  out->pedals_pedal_travel = s.pedals.pedal_travel;
  out->pedals_bse1_psi_unclamped = s.pedals.bse1_psi_unclamped;
  out->pedals_bse2_psi_unclamped = s.pedals.bse2_psi_unclamped;
  out->pedals_bse1_psi = s.pedals.bse1_psi;
  out->pedals_bse2_psi = s.pedals.bse2_psi;
  out->pedals_bse_psi_filtered = s.pedals.bse_psi_filtered;
  out->pedals_brake_light = s.pedals.brake_light;
  out->steering_range_fault = s.steering.range_fault ? 1 : 0;
  out->steering_travel = s.steering.travel;
  out->steering_angle_deg = s.steering.angle_deg;
  out->din_b1_disconnect = s.inputs.b1_disconnect ? 1 : 0;
  out->din_b1_overvoltage = s.inputs.b1_overvoltage ? 1 : 0;
  out->din_b2_disconnect = s.inputs.b2_disconnect ? 1 : 0;
  out->din_b2_overvoltage = s.inputs.b2_overvoltage ? 1 : 0;
  out->din_hard_braking = s.inputs.hard_braking ? 1 : 0;
  out->din_combined_condition = s.inputs.combined_condition ? 1 : 0;
  out->din_fault_confirmed = s.inputs.fault_confirmed ? 1 : 0;
  out->din_latch_state = s.inputs.latch_state ? 1 : 0;
  out->din_linelock_out = s.inputs.linelock_out ? 1 : 0;
  out->din_motor_power = s.inputs.motor_power ? 1 : 0;
  out->din_motor_disconnect = s.inputs.motor_disconnect ? 1 : 0;
  out->din_rtd_pressed = s.inputs.rtd_pressed ? 1 : 0;
  out->dout_linelock_ctrl = s.outputs.linelock_ctrl ? 1 : 0;
  out->dout_amp_enable = s.outputs.amp_enable ? 1 : 0;
  out->dout_bms_led = s.outputs.bms_led ? 1 : 0;
  out->dout_imd_led = s.outputs.imd_led ? 1 : 0;
  out->dout_crit_stb = s.outputs.crit_stb ? 1 : 0;
  out->dout_data_stb = s.outputs.data_stb ? 1 : 0;
  out->torque_lookup = s.torque.lookup;
  out->torque_derated = s.torque.derated;
  out->torque_power_limited = s.torque.power_limited;
  out->torque_traction = s.torque.traction;
  out->torque_power_w = s.torque.power_w;
  out->torque_power_trim_w = s.torque.power_trim_w;
  out->battery_ocv_min_cell_v = s.battery.ocv_min_cell_v;
  out->battery_ocv_max_cell_v = s.battery.ocv_max_cell_v;
  out->battery_soe_pct = s.battery.soe_pct;
  out->battery_net_energy_wh = s.battery.net_energy_wh;
  out->battery_regen_energy_wh = s.battery.regen_energy_wh;
  out->wheel_valid = s.wheel_control.valid ? 1 : 0;
  out->wheel_regen_allowed = s.wheel_control.regen_allowed ? 1 : 0;
  out->wheel_positive_nm = s.wheel_control.positive_nm;
  out->wheel_reference_m_s = s.wheel_control.reference_m_s;
  out->wheel_drive_slip = s.wheel_control.drive_slip;
  out->wheel_braking_slip = s.wheel_control.braking_slip;
}

constexpr uint32_t ElementSize(uint32_t type) {
  return type == VCU_SIL_TYPE_DOUBLE ? 8 : type == VCU_SIL_TYPE_UINT8 ? 1 : 4;
}

#define VCU_SIL_STRUCT(type, name) \
  {name, "", 0, sizeof(type), VCU_SIL_TYPE_STRUCT, 1}
#define VCU_SIL_FIELD(type, name, field, kind)   \
  {name,                                         \
   #field,                                       \
   static_cast<uint32_t>(offsetof(type, field)), \
   static_cast<uint32_t>(sizeof(type::field)),   \
   VCU_SIL_TYPE_##kind,                          \
   static_cast<uint32_t>(sizeof(type::field) /   \
                         ElementSize(VCU_SIL_TYPE_##kind))}

constexpr vcu_sil_field kLayout[] = {
    VCU_SIL_STRUCT(vcu_sil_config, "config"),
    VCU_SIL_FIELD(vcu_sil_config, "config", struct_size, UINT32),
    VCU_SIL_FIELD(vcu_sil_config, "config", event_mode, INT32),

    VCU_SIL_STRUCT(vcu_sil_inputs, "inputs"),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", struct_size, UINT32),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", rtd_pressed, INT32),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", accelerator_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", brake_front_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", brake_rear_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", steering_angle_deg, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", hvc_state, INT32),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", contactors_closed, INT32),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", shutdown_loop_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", wheel_speed_rad_s, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", motor_speed_rad_s, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", motor_torque_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", dc_bus_voltage_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", dc_bus_current_a, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", min_cell_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", max_cell_v, DOUBLE),

    VCU_SIL_STRUCT(vcu_sil_outputs, "outputs"),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", struct_size, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", now_ms, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", control_ms, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_frames, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", frames_dropped, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", inverter_enable, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", direction, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_request_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_limit_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", saturated, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", state, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", faults, UINT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", primary_fault, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", event_mode, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", loop_state, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", motor_speed_valid, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", adc_fresh, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", wheel_control_valid, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", buzzer, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", hard_braking, INT32),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", pedal_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", apps1_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", apps2_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse1_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse2_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse_filtered_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse3_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", steering_angle_deg, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", motor_rpm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_lookup_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_derated_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_power_limited_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_traction_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_command_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", power_w, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", power_trim_w, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", soe_pct, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", wheel_reference_m_s, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", wheel_drive_slip, DOUBLE),

    VCU_SIL_STRUCT(vcu_sil_snapshot, "snapshot"),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", struct_size, UINT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", now_ms, UINT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", state, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", last_transition_id, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", faults, UINT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", primary_fault, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", event_mode, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", loop_state, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", motor_speed_valid, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", adc_fresh, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", inverter_enable, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", buzzer, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", linelock_request, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", brake_flags_latched, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", fault_resets, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", bse3_available, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", launch_armed, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", regen_current_cut, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", motor_rpm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_command_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", brake_bias, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", bse3_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_apps1_counts, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_apps2_counts, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_bse1_counts, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_bse2_counts, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_steering_counts, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_shutdown_in_counts,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_shutdown_out_counts,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_vrefint_counts, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_vdda_from_vrefint,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_apps1_pin_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_apps1_source_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_apps2_pin_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_apps2_source_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_bse1_pin_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_bse1_source_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_bse2_pin_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_bse2_source_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_steering_pin_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_steering_source_v,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_shutdown_in_pin_v,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_shutdown_in_source_v,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_shutdown_out_pin_v,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_shutdown_out_source_v,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", analog_vdda, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps1_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps2_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps1_out_of_range,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps2_out_of_range,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps_mismatch, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps_implausible, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse1_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse2_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse1_out_of_range,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse2_out_of_range,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse1_fault, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse2_fault, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_brake_pressed, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_brake_for_rtd, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_brake_over_throttle,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_firmware_hard_brake,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_hard_brake_mismatch,
                  INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps1_travel_unclamped,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps2_travel_unclamped,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps1_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_apps2_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_pedal_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse1_psi_unclamped,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse2_psi_unclamped,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse1_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse2_psi, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_bse_psi_filtered,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", pedals_brake_light, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", steering_range_fault, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", steering_travel, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", steering_angle_deg, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_b1_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_b1_overvoltage, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_b2_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_b2_overvoltage, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_hard_braking, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_combined_condition, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_fault_confirmed, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_latch_state, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_linelock_out, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_motor_power, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_motor_disconnect, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", din_rtd_pressed, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", dout_linelock_ctrl, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", dout_amp_enable, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", dout_bms_led, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", dout_imd_led, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", dout_crit_stb, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", dout_data_stb, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_lookup, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_derated, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_power_limited, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_traction, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_power_w, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", torque_power_trim_w, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", battery_ocv_min_cell_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", battery_ocv_max_cell_v, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", battery_soe_pct, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", battery_net_energy_wh, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", battery_regen_energy_wh,
                  DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", wheel_valid, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", wheel_regen_allowed, INT32),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", wheel_positive_nm, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", wheel_reference_m_s, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", wheel_drive_slip, DOUBLE),
    VCU_SIL_FIELD(vcu_sil_snapshot, "snapshot", wheel_braking_slip, DOUBLE),

    VCU_SIL_STRUCT(vcu_sil_frame, "frame"),
    VCU_SIL_FIELD(vcu_sil_frame, "frame", t_ms, UINT32),
    VCU_SIL_FIELD(vcu_sil_frame, "frame", id, UINT32),
    VCU_SIL_FIELD(vcu_sil_frame, "frame", bus, INT32),
    VCU_SIL_FIELD(vcu_sil_frame, "frame", from_vcu, INT32),
    VCU_SIL_FIELD(vcu_sil_frame, "frame", len, UINT32),
    VCU_SIL_FIELD(vcu_sil_frame, "frame", data, UINT8),
};

#undef VCU_SIL_FIELD
#undef VCU_SIL_STRUCT

}  // namespace

extern "C" {

int32_t vcu_sil_abi_version(void) { return VCU_SIL_ABI_VERSION; }

int32_t vcu_sil_create(const vcu_sil_config* config, vcu_sil_handle** out) {
  if (config == nullptr || out == nullptr) {
    return VCU_SIL_ERR_NULL;
  }
  if (config->struct_size != sizeof(vcu_sil_config)) {
    return VCU_SIL_ERR_SIZE;
  }
  if (config->event_mode < 0 || config->event_mode > kMaxEventMode) {
    return VCU_SIL_ERR_RANGE;
  }
  // The car constructs its App without parameters, which selects the
  // default event; mode 0 reproduces that boot.
  const lhre::can::EventMode mode =
      config->event_mode == 0
          ? vcu::kDefaultEventMode
          : static_cast<lhre::can::EventMode>(config->event_mode);
  try {
    *out = new vcu_sil_handle(mode);
  } catch (...) {
    return VCU_SIL_ERR_INTERNAL;
  }
  return VCU_SIL_OK;
}

void vcu_sil_destroy(vcu_sil_handle* handle) { delete handle; }

int32_t vcu_sil_advance(vcu_sil_handle* handle, const vcu_sil_inputs* inputs,
                        uint32_t ms, vcu_sil_outputs* outputs) {
  if (handle == nullptr || inputs == nullptr || outputs == nullptr) {
    return VCU_SIL_ERR_NULL;
  }
  if (handle->broken) {
    return VCU_SIL_ERR_INTERNAL;
  }
  if (inputs->struct_size != sizeof(vcu_sil_inputs) ||
      outputs->struct_size != sizeof(vcu_sil_outputs)) {
    return VCU_SIL_ERR_SIZE;
  }
  if (ms < 1 || ms > kMaxAdvanceMs) {
    return VCU_SIL_ERR_RANGE;
  }
  const int32_t check = CheckInputs(*inputs, handle->rig.bench().params);
  if (check != VCU_SIL_OK) {
    return check;
  }
  try {
    const RigOutputs r = handle->rig.Advance(ToRig(*inputs), ms);
    handle->snapshot = r.snapshot;
    FromRig(r, handle->rig.frames_dropped(), outputs);
  } catch (...) {
    handle->broken = true;
    return VCU_SIL_ERR_INTERNAL;
  }
  return VCU_SIL_OK;
}

int32_t vcu_sil_read_console(vcu_sil_handle* handle, char* buffer,
                             uint32_t capacity, uint32_t* length) {
  if (handle == nullptr || length == nullptr ||
      (buffer == nullptr && capacity > 0)) {
    return VCU_SIL_ERR_NULL;
  }
  try {
    if (handle->console.empty()) {
      handle->console = handle->rig.TakeConsole();  // a move: cannot fail
    } else {
      handle->console += handle->rig.TakeConsole();
    }
    const size_t n =
        handle->console.size() < capacity ? handle->console.size() : capacity;
    if (n > 0) {
      std::memcpy(buffer, handle->console.data(), n);
      handle->console.erase(0, n);
    }
    *length = static_cast<uint32_t>(n);
  } catch (...) {
    return VCU_SIL_ERR_INTERNAL;
  }
  return VCU_SIL_OK;
}

int32_t vcu_sil_read_snapshot(vcu_sil_handle* handle, vcu_sil_snapshot* out) {
  if (handle == nullptr || out == nullptr) {
    return VCU_SIL_ERR_NULL;
  }
  if (out->struct_size != sizeof(vcu_sil_snapshot)) {
    return VCU_SIL_ERR_SIZE;
  }
  FromSnapshot(handle->snapshot, out);
  return VCU_SIL_OK;
}

int32_t vcu_sil_read_frames(vcu_sil_handle* handle, vcu_sil_frame* frames,
                            uint32_t frame_size, uint32_t capacity,
                            uint32_t* count) {
  if (handle == nullptr || count == nullptr ||
      (frames == nullptr && capacity > 0)) {
    return VCU_SIL_ERR_NULL;
  }
  if (frame_size != sizeof(vcu_sil_frame)) {
    return VCU_SIL_ERR_SIZE;
  }
  try {
    const std::vector<vcu::sil::CapturedFrame> taken =
        handle->rig.TakeFrames(capacity);
    for (size_t i = 0; i < taken.size(); ++i) {
      const vcu::sil::CapturedFrame& c = taken[i];
      vcu_sil_frame& f = frames[i];
      f.t_ms = c.t_ms;
      f.id = c.id;
      f.bus = c.bus == vcu::sil::CapturedFrame::kData ? VCU_SIL_BUS_DATA
                                                      : VCU_SIL_BUS_CRITICAL;
      f.from_vcu = c.from_vcu ? 1 : 0;
      f.len = c.len;
      std::memcpy(f.data, c.data, sizeof(f.data));
    }
    *count = static_cast<uint32_t>(taken.size());
  } catch (...) {
    return VCU_SIL_ERR_INTERNAL;
  }
  return VCU_SIL_OK;
}

int32_t vcu_sil_layout(uint32_t index, vcu_sil_field* out) {
  if (out == nullptr) {
    return VCU_SIL_ERR_NULL;
  }
  if (index >= sizeof(kLayout) / sizeof(kLayout[0])) {
    return VCU_SIL_ERR_RANGE;
  }
  *out = kLayout[index];
  return VCU_SIL_OK;
}

const char* vcu_sil_build_git_sha(void) { return lhre::kBuildInfo.git_sha; }

const char* vcu_sil_build_git_describe(void) {
  return lhre::kBuildInfo.git_describe;
}

int32_t vcu_sil_build_dirty(void) { return lhre::kBuildInfo.dirty ? 1 : 0; }

const char* vcu_sil_state_name(int32_t state) {
  if (state < 0 || state > 255) {
    return "?";
  }
  return lhre::can::ToString(static_cast<lhre::can::VcuStateMachine>(state));
}

const char* vcu_sil_fault_name(int32_t fault_code) {
  if (fault_code < 0 || fault_code > 255) {
    return "?";
  }
  return lhre::can::ToString(static_cast<lhre::can::VcuFaultCode>(fault_code));
}

const char* vcu_sil_transition_reason(int32_t id) {
  if (id == -1) {
    return "boot";
  }
  if (id < 0 || static_cast<size_t>(id) >= vcu::StateMachine::table_size()) {
    return "?";
  }
  return vcu::StateMachine::table()[id].reason;
}

const char* vcu_sil_error_name(int32_t error) {
  switch (error) {
    case VCU_SIL_OK:
      return "OK";
    case VCU_SIL_ERR_NULL:
      return "ERR_NULL";
    case VCU_SIL_ERR_SIZE:
      return "ERR_SIZE";
    case VCU_SIL_ERR_NON_FINITE:
      return "ERR_NON_FINITE";
    case VCU_SIL_ERR_RANGE:
      return "ERR_RANGE";
    case VCU_SIL_ERR_INTERNAL:
      return "ERR_INTERNAL";
    default:
      return "?";
  }
}

}  // extern "C"
