#pragma once

/* C ABI of libvcu_sil.so: the unmodified VCU app on host fakes, stepped in
 * 1 ms passes by a co-simulation host. See README.md next to this file.
 *
 * config, inputs and outputs start with struct_size, which the caller sets
 * to sizeof(struct). Functions returning int32_t return VCU_SIL_OK or a
 * negative error; a call rejected for its arguments (NULL, size, range,
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

#define VCU_SIL_ABI_VERSION 1

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
  uint32_t now_ms;        /* simulated time after the advance */
  uint32_t control_ms;    /* time of the last control tick */
  uint32_t torque_frames; /* INVERTER_TORQUE_COMMAND frames this advance */
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

typedef struct {
  const char* structure; /* "config", "inputs" or "outputs" */
  const char* field;     /* "" for the whole struct */
  uint32_t offset;
  uint32_t size;
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

#ifdef __cplusplus
}
#endif
