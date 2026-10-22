/* C ABI for running the VCU application inside a vehicle simulation
 * (software-in-the-loop). Scalars only, so a Modelica `external "C"`
 * function can call it without marshalling. See README.md next to this
 * file. */
#ifndef BOARDS_VCU_SIL_VCU_SIL_H_
#define BOARDS_VCU_SIL_VCU_SIL_H_

#ifdef __cplusplus
extern "C" {
#endif

enum {
  VCU_SIL_OK = 0,
  VCU_SIL_ERR_NULL_ARGUMENT = -1,
  /* Also returned for negative time: the App's clock starts at 0 s. */
  VCU_SIL_ERR_TIME_WENT_BACKWARDS = -2,
  VCU_SIL_ERR_NON_FINITE_INPUT = -3,
  /* The App sent no VcuStatus, so there is no torque command to report. */
  VCU_SIL_ERR_NO_STATUS_FROM_APP = -4,
};

/* Returns NULL if allocation fails. */
void* vcu_sil_create(void);
/* NULL is a no-op. */
void vcu_sil_destroy(void* handle);

/* Advances the App's clock to time_s, feeds it the plant state, runs one
 * App::Step(), and reports the latest command it put on the bus.
 * Pedals are fractions 0..1. Torque is at the motor shaft; regen_limit_nm
 * is a positive magnitude. Returns VCU_SIL_OK or a negative error code;
 * outputs are written only on VCU_SIL_OK. */
int vcu_sil_step(void* handle, double time_s, double accel_pedal,
                 double brake_pedal, double motor_speed_rad_s,
                 double pack_voltage_v, double pack_current_a,
                 double* motor_torque_nm, double* regen_limit_nm);

#ifdef __cplusplus
}
#endif

#endif /* BOARDS_VCU_SIL_VCU_SIL_H_ */
