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
#include "test/bench.hpp"

struct vcu_sil_handle {
  explicit vcu_sil_handle(lhre::can::EventMode mode) : rig(mode) {}

  vcu::sil::Rig rig;
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

void FromRig(const RigOutputs& r, vcu_sil_outputs* out) {
  const vcu::ControlSnapshot& s = r.snapshot;
  out->now_ms = r.now_ms;
  out->control_ms = s.now_ms;
  out->torque_frames = r.torque_frames;
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

#define VCU_SIL_STRUCT(type, name) {name, "", 0, sizeof(type)}
#define VCU_SIL_FIELD(type, name, field)                       \
  {name, #field, static_cast<uint32_t>(offsetof(type, field)), \
   static_cast<uint32_t>(sizeof(type::field))}

constexpr vcu_sil_field kLayout[] = {
    VCU_SIL_STRUCT(vcu_sil_config, "config"),
    VCU_SIL_FIELD(vcu_sil_config, "config", struct_size),
    VCU_SIL_FIELD(vcu_sil_config, "config", event_mode),

    VCU_SIL_STRUCT(vcu_sil_inputs, "inputs"),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", struct_size),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", rtd_pressed),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", accelerator_travel),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", brake_front_psi),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", brake_rear_psi),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", steering_angle_deg),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", hvc_state),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", contactors_closed),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", shutdown_loop_v),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", wheel_speed_rad_s),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", motor_speed_rad_s),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", motor_torque_nm),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", dc_bus_voltage_v),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", dc_bus_current_a),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", min_cell_v),
    VCU_SIL_FIELD(vcu_sil_inputs, "inputs", max_cell_v),

    VCU_SIL_STRUCT(vcu_sil_outputs, "outputs"),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", struct_size),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", now_ms),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", control_ms),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_frames),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", inverter_enable),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", direction),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_request_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_limit_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", saturated),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", state),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", faults),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", primary_fault),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", event_mode),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", loop_state),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", motor_speed_valid),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", adc_fresh),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", wheel_control_valid),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", buzzer),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", hard_braking),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", pedal_travel),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", apps1_travel),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", apps2_travel),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse1_psi),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse2_psi),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse_filtered_psi),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", bse3_psi),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", steering_angle_deg),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", motor_rpm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_lookup_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_derated_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_power_limited_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_traction_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", torque_command_nm),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", power_w),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", power_trim_w),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", soe_pct),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", wheel_reference_m_s),
    VCU_SIL_FIELD(vcu_sil_outputs, "outputs", wheel_drive_slip),
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
    FromRig(handle->rig.Advance(ToRig(*inputs), ms), outputs);
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
