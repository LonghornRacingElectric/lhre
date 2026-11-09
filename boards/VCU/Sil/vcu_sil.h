#pragma once

/* C ABI of libvcu_sil.so: the unmodified VCU app on host fakes, stepped in
 * 1 ms passes by a co-simulation host. See README.md next to this file.
 *
 * config, inputs, outputs and snapshot start with struct_size, which the
 * caller sets to sizeof(struct). Functions returning int32_t return VCU_SIL_OK
 * or a negative error; a call rejected for its arguments (NULL, size, range,
 * non-finite) changes nothing. vcu_sil_layout() lists every field's offset
 * so a binding in another language can check its mirror field by field.
 * Bump VCU_SIL_ABI_VERSION on any change to this file.
 *
 * Call from one thread at a time, across all handles in the process: the
 * host FreeRTOS port the app links keeps process-wide state. */

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VCU_SIL_ABI_VERSION 2

enum {
  VCU_SIL_OK = 0,
  VCU_SIL_ERR_NULL = -1,       /* a required pointer is NULL */
  VCU_SIL_ERR_SIZE = -2,       /* struct_size does not match this library */
  VCU_SIL_ERR_NON_FINITE = -3, /* an input is NaN or infinite */
  VCU_SIL_ERR_RANGE = -4,      /* an input is outside its documented range */
  VCU_SIL_ERR_INTERNAL = -5    /* the library failed; the handle is unusable */
};

/* Bits of vcu_sil_outputs.saturated: a car-model value had to be clamped to
 * what its sensor or CAN field can carry during the last advance. */
enum {
  VCU_SIL_SAT_WHEEL_SPEED = 1 << 0,
  VCU_SIL_SAT_MOTOR_SPEED = 1 << 1,
  VCU_SIL_SAT_MOTOR_TORQUE = 1 << 2,
  VCU_SIL_SAT_BUS_VOLTAGE = 1 << 3,
  VCU_SIL_SAT_BUS_CURRENT = 1 << 4,
  VCU_SIL_SAT_CELL_VOLTAGE = 1 << 5,
  VCU_SIL_SAT_STEERING = 1 << 6
};

typedef struct vcu_sil_handle vcu_sil_handle;

typedef struct {
  uint32_t struct_size;
  /* lhre EventMode 1..4 selects the firmware's own parameter set for that
   * event. 0 boots like the car, which starts in the default event
   * (ENDURANCE). */
  int32_t event_mode;
} vcu_sil_config;

/* Held constant for the whole advance. */
typedef struct {
  uint32_t struct_size;
  /* Driver */
  int32_t rtd_pressed;       /* ready-to-drive switch, nonzero = pressed */
  double accelerator_travel; /* 0..1, both APPS */
  double brake_front_psi;    /* BSE1, 0..BSE full scale */
  double brake_rear_psi;     /* BSE2 and the PDU's BSE3, same range */
  double steering_angle_deg; /* steering column; clamped to what the sensor
                              * can show, see VCU_SIL_SAT_STEERING */
  /* Rest of the car */
  int32_t hvc_state;         /* lhre HvcState, 0..4 */
  int32_t contactors_closed; /* both AIRs, nonzero = closed */
  double shutdown_loop_v;    /* both sense taps, 0..27 */
  /* Car model, signed as the model reports them */
  double wheel_speed_rad_s[4]; /* FL, FR, RL, RR, positive forward */
  double motor_speed_rad_s;    /* positive forward */
  double motor_torque_nm;      /* shaft torque, positive driving */
  double dc_bus_voltage_v;
  double dc_bus_current_a; /* positive discharging */
  double min_cell_v;
  double max_cell_v;
} vcu_sil_inputs;

/* The firmware's view after the last 1 ms pass of an advance. */
typedef struct {
  uint32_t struct_size;
  uint32_t now_ms;         /* simulated time after the advance */
  uint32_t control_ms;     /* time of the last control tick */
  uint32_t torque_frames;  /* INVERTER_TORQUE_COMMAND frames this advance */
  uint32_t frames_dropped; /* captured frames lost to the buffer limit, ever */
  /* Last INVERTER_TORQUE_COMMAND of the advance, decoded from the bus */
  int32_t inverter_enable;
  int32_t direction;
  double torque_request_nm;
  double torque_limit_nm;
  uint32_t saturated; /* VCU_SIL_SAT_* bits */
  /* Firmware internals from the control snapshot */
  int32_t state;         /* lhre VcuStateMachine */
  uint32_t faults;       /* bit i is lhre VcuFaultCode i + 1 */
  int32_t primary_fault; /* lhre VcuFaultCode */
  int32_t event_mode;
  int32_t loop_state; /* lhre ShutdownLoopState */
  int32_t motor_speed_valid;
  int32_t adc_fresh;
  int32_t wheel_control_valid;
  int32_t buzzer;
  int32_t hard_braking; /* BSPD comparator pin, as the firmware read it */
  double pedal_travel;  /* filtered, after the deadzone: the torque input */
  double apps1_travel;
  double apps2_travel;
  double bse1_psi;
  double bse2_psi;
  double bse_filtered_psi;
  double bse3_psi;
  double steering_angle_deg;
  double motor_rpm;
  double torque_lookup_nm;
  double torque_derated_nm;
  double torque_power_limited_nm;
  double torque_traction_nm;
  double torque_command_nm; /* before frame rounding */
  double power_w;           /* inverter-reported V x I */
  double power_trim_w;
  double soe_pct;
  double wheel_reference_m_s;
  double wheel_drive_slip;
} vcu_sil_outputs;

/* Everything the firmware's control tick published (ControlSnapshot), field
 * for field: bools and enums as int32_t, floats as double. Read it with
 * vcu_sil_read_snapshot() after an advance. */
typedef struct {
  uint32_t struct_size;
  /* Status */
  uint32_t now_ms;
  int32_t state;
  /* The last transition's reason as an id for vcu_sil_transition_reason():
   * the first state-machine table row with that text, so two transitions
   * worded alike share an id. Not a row index; -1 is "boot". */
  int32_t last_transition_id;
  uint32_t faults;
  int32_t primary_fault;
  int32_t event_mode;
  int32_t loop_state;
  int32_t motor_speed_valid;
  int32_t adc_fresh;
  int32_t inverter_enable;
  int32_t buzzer;
  int32_t linelock_request;
  int32_t brake_flags_latched;
  int32_t fault_resets;
  int32_t bse3_available;
  int32_t launch_armed;
  int32_t regen_current_cut;
  double motor_rpm;
  double torque_command_nm;
  double brake_bias;
  double bse3_psi;
  /* Analog front end: raw counts, MCU pin volts, sensor-side volts */
  int32_t analog_apps1_counts;
  int32_t analog_apps2_counts;
  int32_t analog_bse1_counts;
  int32_t analog_bse2_counts;
  int32_t analog_steering_counts;
  int32_t analog_shutdown_in_counts;
  int32_t analog_shutdown_out_counts;
  int32_t analog_vrefint_counts;
  int32_t analog_vdda_from_vrefint;
  double analog_apps1_pin_v;
  double analog_apps1_source_v;
  double analog_apps2_pin_v;
  double analog_apps2_source_v;
  double analog_bse1_pin_v;
  double analog_bse1_source_v;
  double analog_bse2_pin_v;
  double analog_bse2_source_v;
  double analog_steering_pin_v;
  double analog_steering_source_v;
  double analog_shutdown_in_pin_v;
  double analog_shutdown_in_source_v;
  double analog_shutdown_out_pin_v;
  double analog_shutdown_out_source_v;
  double analog_vdda;
  /* Pedals */
  int32_t pedals_apps1_disconnect;
  int32_t pedals_apps2_disconnect;
  int32_t pedals_apps1_out_of_range;
  int32_t pedals_apps2_out_of_range;
  int32_t pedals_apps_mismatch;
  int32_t pedals_apps_implausible;
  int32_t pedals_bse1_disconnect;
  int32_t pedals_bse2_disconnect;
  int32_t pedals_bse1_out_of_range;
  int32_t pedals_bse2_out_of_range;
  int32_t pedals_bse1_fault;
  int32_t pedals_bse2_fault;
  int32_t pedals_brake_pressed;
  int32_t pedals_brake_for_rtd;
  int32_t pedals_brake_over_throttle;
  int32_t pedals_firmware_hard_brake;
  int32_t pedals_hard_brake_mismatch;
  double pedals_apps1_travel_unclamped;
  double pedals_apps2_travel_unclamped;
  double pedals_apps1_travel;
  double pedals_apps2_travel;
  double pedals_pedal_travel;
  double pedals_bse1_psi_unclamped;
  double pedals_bse2_psi_unclamped;
  double pedals_bse1_psi;
  double pedals_bse2_psi;
  double pedals_bse_psi_filtered;
  double pedals_brake_light;
  /* Steering */
  int32_t steering_range_fault;
  double steering_travel;
  double steering_angle_deg;
  /* Digital inputs as the firmware uses them: polarity applied (true =
   * asserted), and debounced where it debounces (the brake flags, motor
   * power and disconnect, RTD); the BSPD readbacks are raw. */
  int32_t din_b1_disconnect;
  int32_t din_b1_overvoltage;
  int32_t din_b2_disconnect;
  int32_t din_b2_overvoltage;
  int32_t din_hard_braking;
  int32_t din_combined_condition;
  int32_t din_fault_confirmed;
  int32_t din_latch_state;
  int32_t din_linelock_out;
  int32_t din_motor_power;
  int32_t din_motor_disconnect;
  int32_t din_rtd_pressed;
  /* Digital outputs (true = pin high) */
  int32_t dout_linelock_ctrl;
  int32_t dout_amp_enable;
  int32_t dout_bms_led;
  int32_t dout_imd_led;
  int32_t dout_crit_stb;
  int32_t dout_data_stb;
  /* Torque path */
  double torque_lookup;
  double torque_derated;
  double torque_power_limited;
  double torque_traction;
  double torque_power_w;
  double torque_power_trim_w;
  /* Battery estimate */
  double battery_ocv_min_cell_v;
  double battery_ocv_max_cell_v;
  double battery_soe_pct;
  double battery_net_energy_wh;
  double battery_regen_energy_wh;
  /* Wheel-slip control */
  int32_t wheel_valid;
  int32_t wheel_regen_allowed;
  double wheel_positive_nm;
  double wheel_reference_m_s;
  double wheel_drive_slip;
  double wheel_braking_slip;
} vcu_sil_snapshot;

/* One CAN frame on the rig's buses, as vcu_sil_read_frames() returns it. */
typedef struct {
  uint32_t t_ms;    /* simulated time it was sent */
  uint32_t id;      /* standard 11-bit ID */
  int32_t bus;      /* VCU_SIL_BUS_* */
  int32_t from_vcu; /* 1: the VCU sent it; 0: the rig, as the rest of the car */
  uint32_t len;     /* 0..8 */
  uint8_t data[8];
} vcu_sil_frame;

enum { VCU_SIL_BUS_CRITICAL = 0, VCU_SIL_BUS_DATA = 1 };

/* vcu_sil_field.type */
enum {
  VCU_SIL_TYPE_STRUCT = 0,
  VCU_SIL_TYPE_UINT32 = 1,
  VCU_SIL_TYPE_INT32 = 2,
  VCU_SIL_TYPE_DOUBLE = 3,
  VCU_SIL_TYPE_UINT8 = 4
};

typedef struct {
  const char*
      structure;     /* "config", "inputs", "outputs", "snapshot", "frame" */
  const char* field; /* "" for the whole struct */
  uint32_t offset;
  uint32_t size;
  uint32_t type;  /* VCU_SIL_TYPE_* of one element */
  uint32_t count; /* elements: 1, or the array length */
} vcu_sil_field;

int32_t vcu_sil_abi_version(void);

int32_t vcu_sil_create(const vcu_sil_config* config, vcu_sil_handle** out);

/* NULL is a no-op. */
void vcu_sil_destroy(vcu_sil_handle* handle);

/* Runs `ms` 1 ms passes (1..1000) with `inputs` held. VCU_SIL_ERR_INTERNAL
 * means the handle is unusable; destroy it. */
int32_t vcu_sil_advance(vcu_sil_handle* handle, const vcu_sil_inputs* inputs,
                        uint32_t ms, vcu_sil_outputs* outputs);

/* Moves up to `capacity` bytes of the firmware's console (debug UART) text
 * into `buffer`, not NUL-terminated, and sets *length. Call until *length
 * is 0. The rig keeps the newest 1 MiB between reads. */
int32_t vcu_sil_read_console(vcu_sil_handle* handle, char* buffer,
                             uint32_t capacity, uint32_t* length);

/* The control snapshot after the last advance; before the first, the
 * firmware's defaults (INIT, last_transition_id -1). */
int32_t vcu_sil_read_snapshot(vcu_sil_handle* handle, vcu_sil_snapshot* out);

/* Moves up to `capacity` captured frames, oldest first, into `frames` and
 * sets *count; `frame_size` is sizeof(vcu_sil_frame). Call until *count is
 * 0. Every frame on both buses is captured, in send order: in each 1 ms pass
 * the rig's frames go out before App::Step() and the VCU's during it. The
 * rig keeps the newest 65536 between reads; outputs.frames_dropped counts
 * the rest. */
int32_t vcu_sil_read_frames(vcu_sil_handle* handle, vcu_sil_frame* frames,
                            uint32_t frame_size, uint32_t capacity,
                            uint32_t* count);

/* Entry `index` of the layout table; VCU_SIL_ERR_RANGE past the end. */
int32_t vcu_sil_layout(uint32_t index, vcu_sil_field* out);

/* What the library was built from, stamped by the workspace status command
 * (tools/workspace_status.sh): "unknown" without git. Static strings. */
const char* vcu_sil_build_git_sha(void);
const char* vcu_sil_build_git_describe(void);
int32_t vcu_sil_build_dirty(void);

/* Wire names from the CAN spec, "?" when out of range. Static strings. */
const char* vcu_sil_state_name(int32_t state);
const char* vcu_sil_fault_name(int32_t fault_code);
const char* vcu_sil_error_name(int32_t error);
/* The state machine's reason for snapshot.last_transition_id: "boot" for
 * -1, "?" when out of range. Static strings. */
const char* vcu_sil_transition_reason(int32_t id);

#ifdef __cplusplus
}
#endif
