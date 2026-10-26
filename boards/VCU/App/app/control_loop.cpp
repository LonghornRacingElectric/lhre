#include "app/vcu_app.hpp"
#include "hmi/status_led.hpp"
#include "longhorn/math.hpp"

namespace vcu {

using lhre::can::EventMode;
using lhre::can::ShutdownLoopState;

void App::ControlTick(uint32_t now) {
  if (!control_started_) {
    control_started_ = true;
    boot_ms_ = now;
    last_control_ms_ = now;
  }
  const uint32_t dt_ms = now - last_control_ms_;
  last_control_ms_ = now;

  DrainCan(now);
  const bool rtd_edge = ReadInputs(now);
  UpdateBrakeLatch(now);

  const bool inverter_fresh = rx_.InverterFresh(now);
  const bool inverter_faulted = rx_.InverterFaulted(now);
  faults_ = ComputeFaults(now, inverter_fresh, inverter_faulted);
  UpdateState(now, rtd_edge, inverter_fresh, inverter_faulted);
  ApplyModeRequests();

  UpdateTorque(now, dt_ms, inverter_fresh);
  const Rgb status = StatusColor(sm_.state(), PrimaryFault(faults_), now);
  rainbow_.Override(status.r, status.g, status.b);

  UpdateStatus(now);
  DriveOutputs(now);
  SendCritical(now);
  Publish();
  control_beats_.fetch_add(1);
}

void App::DrainCan(uint32_t now) {
  lhal::CanFrame frame;
  while (critical_inbox_.Pop(&frame)) {
    rx_.Dispatch(frame, now);
  }
  while (data_inbox_.Pop(&frame)) {
    wheels_.Dispatch(frame, now);
  }
}

bool App::ReadInputs(uint32_t now) {
  const Params& P = *params_;
  live_.analog = ReadAnalog(p_.adc1, p_.adc2, p_.vrefint_cal, P.analog);
  const bool adc1_fresh = adc1_progress_.Fresh(p_.adc1, now);
  const bool adc2_fresh = adc2_progress_.Fresh(p_.adc2, now);
  live_.adc_fresh = adc1_fresh && adc2_fresh;
  const AnalogReadings& a = live_.analog;
  DigitalInputs raw = ReadDigitalInputs(p_.inputs);
  DigitalInputs& in = live_.inputs;
  in = raw;  // BSPD readbacks are taken as-is: the latch must act at once
  in.b1_disconnect = b1_disconnect_.Update(raw.b1_disconnect, now,
                                           P.digital.brake_flag_debounce_ms);
  in.b1_overvoltage = b1_overvoltage_.Update(raw.b1_overvoltage, now,
                                             P.digital.brake_flag_debounce_ms);
  in.b2_disconnect = b2_disconnect_.Update(raw.b2_disconnect, now,
                                           P.digital.brake_flag_debounce_ms);
  in.b2_overvoltage = b2_overvoltage_.Update(raw.b2_overvoltage, now,
                                             P.digital.brake_flag_debounce_ms);
  // The disconnected cable can float; verify polarity and add an electrical
  // pull.
  in.motor_power =
      motor_power_.Update(raw.motor_power, now, P.digital.motor_debounce_ms);
  in.motor_disconnect = motor_disconnect_.Update(raw.motor_disconnect, now,
                                                 P.digital.motor_debounce_ms);
  const bool rtd_edge =
      rtd_switch_.Update(raw.rtd_pressed, now, P.digital.rtd_debounce_ms);
  in.rtd_pressed = rtd_switch_.pressed();

  PedalInputs pin;
  pin.apps1_v = a.apps1.source_v;
  pin.apps2_v = a.apps2.source_v;
  pin.bse1_v = a.bse1.source_v;
  pin.bse2_v = a.bse2.source_v;
  pin.bse1_pin_v = a.bse1.pin_v;
  pin.bse2_pin_v = a.bse2.pin_v;
  pin.hard_braking_hw = in.hard_braking;
  live_.pedals = pedals_.Update(pin, now, P.apps, P.bse);
  const PedalOutputs& ped = live_.pedals;
  live_.steering = ComputeSteering(a.steering.source_v, P.steering);
  live_.brake_bias =
      brake_bias_.Update(ped.bse1_psi, ped.bse2_psi, P.bse.bias_min_total_psi);
  live_.loop = shutdown_loop_.Update(a.shutdown_in.pin_v, a.shutdown_out.pin_v,
                                     now, P.shutdown);
  return rtd_edge;
}

void App::UpdateBrakeLatch(uint32_t now) {
  const DigitalInputs& in = live_.inputs;
  // Brake comparator soft latch: cleared only by the dash's reset.
  const bool dui_fresh = rx_.dui_driver_inputs.Fresh(now);
  const bool reset_level = dui_fresh && rx_.dui_driver_inputs.msg.fault_reset;
  const bool reset_edge = reset_level && !prev_fault_reset_;
  prev_fault_reset_ = reset_level;
  brake_latch_.Update(in.b1_disconnect || in.b1_overvoltage ||
                          in.b2_disconnect || in.b2_overvoltage,
                      reset_edge);
  if (brake_latch_.dirty()) {
    latch_save_value_.store(brake_latch_.persisted_value());
    latch_save_pending_.store(true);
    brake_latch_.MarkPersisted();
  }

  // BSE3 from the PDU: regen blending needs it; no blending without it.
  live_.bse3_available = rx_.bse_3.Fresh(now);
  live_.bse3_psi =
      live_.bse3_available ? rx_.bse_3.msg.brake_pressure_3() : 0.0f;
}

uint32_t App::ComputeFaults(uint32_t now, bool inverter_fresh,
                            bool inverter_faulted) const {
  const PedalOutputs& ped = live_.pedals;
  uint32_t f = 0;
  if (!live_.adc_fresh) f |= kFaultAdcStale;
  if (ped.apps_implausible) f |= kFaultAppsImplausible;
  if (ped.apps1_disconnect || ped.apps1_out_of_range) f |= kFaultApps1Range;
  if (ped.apps2_disconnect || ped.apps2_out_of_range) f |= kFaultApps2Range;
  if (ped.bse1_fault) f |= kFaultBse1Range;
  if (ped.bse2_fault) f |= kFaultBse2Range;
  if (live_.steering.range_fault) f |= kFaultSteeringRange;
  if (brake_latch_.latched()) f |= kFaultBrakeFlagLatched;
  if (ped.brake_over_throttle) f |= kFaultBrakeOverThrottle;
  if (!shutdown_loop_.closed()) f |= kFaultShutdownOpen;
  if (!rx_.contactor_status.Fresh(now)) f |= kFaultHvcTimeout;
  if (!inverter_fresh) f |= kFaultInverterTimeout;
  if (!rx_.bse_3.Fresh(now)) f |= kFaultPduTimeout | kFaultBse3Unavailable;
  if (inverter_faulted) f |= kFaultInverterFault;
  if (live_.inputs.latch_state) f |= kFaultBspdTripped;
  if (ped.hard_brake_mismatch) f |= kFaultHardBrakeMismatch;
  if (!rx_.dui_driver_inputs.Fresh(now)) f |= kFaultDuiTimeout;
  if (live_.inputs.motor_disconnect) f |= kFaultMotorDisconnect;
  if (live_.loop == ShutdownLoopState::kImplausible) {
    f |= kFaultShutdownSenseImplausible;
  }
  StateInputs hvc;
  hvc.hvc_fresh = rx_.contactor_status.Fresh(now);
  hvc.hvc_state = static_cast<lhre::can::HvcState>(
      rx_.contactor_status.msg.hvc_state_machine);
  hvc.contactors_closed = rx_.contactor_status.msg.positive_hv_contactor &&
                          rx_.contactor_status.msg.negative_hv_contactor;
  if (sm_.state() == VcuState::kDrive && !StateConditions::Energized(hvc)) {
    f |= kFaultContactorsOpen;
  }
  return f;
}

void App::UpdateState(uint32_t now, bool rtd_edge, bool inverter_fresh,
                      bool inverter_faulted) {
  const Params& P = *params_;
  const DigitalInputs& in = live_.inputs;
  const PedalOutputs& ped = live_.pedals;
  StateInputs si;
  si.bspd_latched = in.latch_state;
  si.hvc_fresh = rx_.contactor_status.Fresh(now);
  si.hvc_state = static_cast<lhre::can::HvcState>(
      rx_.contactor_status.msg.hvc_state_machine);
  si.contactors_closed = rx_.contactor_status.msg.positive_hv_contactor &&
                         rx_.contactor_status.msg.negative_hv_contactor;
  si.loop_closed = shutdown_loop_.closed();
  si.torque_cut_fault = (faults_ & kTorqueCutFaults) != 0;
  si.inverter_ready = inverter_fresh && !inverter_faulted;
  si.pdu_fresh = live_.bse3_available;
  si.bse_valid = !ped.bse1_fault && !ped.bse2_fault && !ped.bse1_disconnect &&
                 !ped.bse2_disconnect && !ped.bse1_out_of_range &&
                 !ped.bse2_out_of_range;
  si.brake_for_rtd = ped.brake_for_rtd;
  si.pedal_released = ped.pedal_travel <= P.drive.rtd_max_pedal;
  si.rtd_edge = rtd_edge;
  si.rtd_pressed = in.rtd_pressed;
  si.rtd_release_exits_drive = P.drive.rtd_release_exits_drive;

  const VcuState before = sm_.state();
  if (sm_.Step(si)) {
    logger_.Info("state %s -> %s (%s)", lhre::can::ToString(before),
                 lhre::can::ToString(sm_.state()), sm_.last_reason());
    if (sm_.state() == VcuState::kDrive) {
      buzzer_.Start(now, P.buzzer.duration_ms);
    } else {
      buzzer_.Stop();
    }
    if (before == VcuState::kDrive) {
      torque_path_.Reset();
    }
  }
}

void App::ApplyModeRequests() {
  if (rx_.vcu_mode_command.count != handled_mode_commands_) {
    handled_mode_commands_ = rx_.vcu_mode_command.count;
    pending_mode_ = static_cast<int>(rx_.vcu_mode_command.msg.event_mode);
  }
  const int shell_request = shell_mode_request_.exchange(-1);
  if (shell_request >= 0) {
    pending_mode_ = shell_request;
  }
  // lhre-2026 switched parameter sets only in PARK; never mid-drive.
  if (pending_mode_ < 0 || sm_.state() == VcuState::kDrive) {
    return;
  }
  const auto mode = static_cast<EventMode>(pending_mode_);
  pending_mode_ = -1;
  const Params& next = ParamsFor(mode);
  if (&next != params_) {
    params_ = &next;
    torque_path_.Reset();
    logger_.Info("event mode -> %s", lhre::can::ToString(next.event_mode));
  }
  live_.event_mode = params_->event_mode;
}

void App::UpdateTorque(uint32_t now, uint32_t dt_ms, bool inverter_fresh) {
  const InverterFeedback fb = ReadInverterFeedback(now);

  BatteryInputs bin;
  bin.dc_bus_current = fb.dc_a;
  bin.dc_bus_voltage = fb.dc_v;
  bin.min_cell_v = rx_.battery_cell_limits.seen()
                       ? rx_.battery_cell_limits.msg.min_cell_voltage()
                       : 0.0f;
  bin.max_cell_v = rx_.battery_cell_limits.seen()
                       ? rx_.battery_cell_limits.msg.max_cell_voltage()
                       : 0.0f;
  bin.dt_ms = dt_ms;
  live_.battery = battery_.Update(bin, params_->battery);

  // Torque only in DRIVE with no torque-cut fault and no brake-over-throttle.
  const bool torque_allowed =
      sm_.state() == VcuState::kDrive && (faults_ & kTorqueCutFaults) == 0 &&
      !live_.pedals.brake_over_throttle && !bootloader_entered_.load() &&
      !bootloader_request_.load();
  const float positive = PositiveTorque(now, dt_ms, torque_allowed, fb);
  ArbitrateTorque(now, dt_ms, torque_allowed, inverter_fresh, positive, fb);
}

App::InverterFeedback App::ReadInverterFeedback(uint32_t now) {
  InverterFeedback fb;
  live_.motor_speed_valid = rx_.MotorRpm(now, &fb.rpm);
  live_.motor_rpm = fb.rpm;
  // Values may be held for diagnostics, but every required feedback frame
  // must be fresh for DRIVE (including the slower 10 Hz current frame).
  fb.dc_v = rx_.inverter_voltage.seen()
                ? rx_.inverter_voltage.msg.dc_bus_voltage()
                : 0.0f;
  fb.dc_a = rx_.inverter_current.seen()
                ? rx_.inverter_current.msg.dc_bus_current()
                : 0.0f;
  return fb;
}

float App::PositiveTorque(uint32_t now, uint32_t dt_ms, bool torque_allowed,
                          const InverterFeedback& fb) {
  const Params& P = *params_;
  const PedalOutputs& ped = live_.pedals;
  TorqueInputs tin;
  tin.pedal_travel = ped.pedal_travel;
  tin.motor_rpm = fb.rpm;
  tin.motor_speed_valid = live_.motor_speed_valid;
  tin.dc_bus_voltage = fb.dc_v;
  tin.dc_bus_current = fb.dc_a;
  tin.power_sample_ms = rx_.inverter_current.last_ms;
  tin.ocv_min_cell_v = live_.battery.ocv_min_cell_v;
  tin.dt_ms = dt_ms;
  tin.speed_from_high_speed = rx_.inverter_speed.Fresh(now);
  tin.speed_sample_ms = tin.speed_from_high_speed ? rx_.inverter_speed.last_ms
                                                  : rx_.inverter_status.last_ms;
  TractionParams acceleration_limiter = P.traction;
  if (P.wheel_control.enabled) acceleration_limiter.enabled = false;
  live_.torque =
      torque_path_.Update(tin, P.torque, P.power_limit, acceleration_limiter);

  WheelInputs win;
  win.fresh = wheels_.Fresh(now);
  win.rad_s[0] =
      wheels_.acceleration_vector_unsprung_wheel_speed_fl.msg.wheel_speed();
  win.rad_s[1] =
      wheels_.acceleration_vector_unsprung_wheel_speed_fr.msg.wheel_speed();
  win.rad_s[2] =
      wheels_.acceleration_vector_unsprung_wheel_speed_rl.msg.wheel_speed();
  win.rad_s[3] =
      wheels_.acceleration_vector_unsprung_wheel_speed_rr.msg.wheel_speed();
  live_.wheel_control = wheel_control_.Update(
      win, torque_allowed ? live_.torque.traction : 0, dt_ms, P.wheel_control);
  if (launch_request_.exchange(false)) {
    const bool armed =
        launch_.Arm(torque_allowed && live_.wheel_control.valid &&
                        live_.wheel_control.reference_m_s < 0.5f &&
                        P.event_mode == EventMode::kAcceleration,
                    ped.brake_for_rtd, ped.pedal_travel, fb.rpm, P.launch);
    logger_.Info(
        "launch %s",
        armed ? "armed" : "refused (disabled or not stationary/braked)");
  }
  if (P.launch.enabled && !live_.wheel_control.valid) launch_.Abort();
  const float launch_torque = launch_.Update(
      live_.wheel_control.positive_nm, torque_allowed, ped.brake_for_rtd,
      ped.pedal_travel, fb.rpm, dt_ms, P.launch);
  live_.launch_armed = launch_.armed();
  return P.launch.enabled && !live_.wheel_control.valid ? 0 : launch_torque;
}

void App::ArbitrateTorque(uint32_t now, uint32_t dt_ms, bool torque_allowed,
                          bool inverter_fresh, float positive_nm,
                          const InverterFeedback& fb) {
  const Params& P = *params_;
  const PedalOutputs& ped = live_.pedals;
  RegenInputs rin;
  rin.drive = torque_allowed;
  rin.feedback_fresh = inverter_fresh && live_.bse3_available &&
                       live_.wheel_control.valid &&
                       live_.wheel_control.regen_allowed &&
                       rx_.battery_cell_limits.Fresh(now) && live_.adc_fresh;
  rin.hard_braking = live_.inputs.hard_braking || ped.firmware_hard_brake;
  rin.linelock_out = live_.inputs.linelock_out;
  rin.pedal = ped.pedal_travel;
  rin.rear_pre_psi = ped.bse2_psi;
  rin.rear_post_psi = live_.bse3_psi;
  rin.motor_rpm = fb.rpm;
  rin.dc_v = fb.dc_v;
  rin.dc_a = fb.dc_a;
  rin.max_cell_v = rx_.battery_cell_limits.msg.max_cell_voltage();
  rin.dt_ms = dt_ms;
  const RegenResult regen = regen_.Update(rin, P.regen);
  live_.regen_current_cut = regen.current_cut;
  const bool regen_braking =
      P.regen.enabled && ped.bse2_psi > P.regen.engage_max_psi;
  torque_command_ = 0.0f;
  if (torque_allowed && !regen.current_cut) {
    if (regen.torque_nm < 0.0f) {
      torque_command_ =
          longhorn::Clamp(regen.torque_nm, -P.inverter.torque_limit_nm, 0.0f);
    } else if (!regen_braking) {
      torque_command_ =
          longhorn::Clamp(positive_nm, 0.0f, P.inverter.torque_limit_nm);
    }
  }
  if (!torque_allowed) torque_path_.Reset();
  live_.torque_command = torque_command_;
  live_.inverter_enable =
      sm_.state() == VcuState::kDrive && (faults_ & kTorqueCutFaults) == 0 &&
      !bootloader_entered_.load() && !bootloader_request_.load();
  live_.linelock_request = torque_allowed && regen.linelock_request;
}

// Fills the status fields before the frames that report them go out.
void App::UpdateStatus(uint32_t now) {
  live_.now_ms = now;
  live_.state = sm_.state();
  live_.last_transition = sm_.last_reason();
  live_.faults = faults_;
  live_.primary_fault = PrimaryFault(faults_);
  live_.brake_flags_latched = brake_latch_.latched();
  live_.fault_resets = brake_latch_.resets();
}

void App::DriveOutputs(uint32_t now) {
  const Params& P = *params_;
  DigitalOutputs& out = live_.outputs;
  out = SafeOutputs();
  out.linelock_ctrl = live_.linelock_request;

  // RTD sound: amp on only while the buzzer runs; cut at once if the loop
  // opens or the state leaves DRIVE (Buzzer::Stop on transition).
  const bool sound =
      buzzer_.Update(now, shutdown_loop_.closed(), P.buzzer.max_on_ms);
  live_.buzzer = sound;
  if (p_.audio_dac != nullptr) {
    if (sound && !dac_playing_) {
      BuildSineTable(sine_, kSineSamples, P.buzzer.amplitude);
      const auto rate = static_cast<uint32_t>(P.buzzer.frequency_hz *
                                              static_cast<float>(kSineSamples));
      dac_playing_ = lhal::IsOk(p_.audio_dac->Play(sine_, kSineSamples, rate));
    } else if (!sound && dac_playing_) {
      p_.audio_dac->Stop(kDacMidScale);
      dac_playing_ = false;
    }
  }
  out.amp_enable = sound;

  // BMS/IMD LEDs only ever add indication over the board default (on).
  error_leds_.Update(rx_.indicators_shutdown_status.Fresh(now),
                     rx_.indicators_shutdown_status.msg.bms_error,
                     rx_.indicators_shutdown_status.msg.imd_error);
  out.bms_led = error_leds_.bms_on();
  out.imd_led = error_leds_.imd_on();

  WriteDigitalOutputs(p_.outputs, out);
}

}  // namespace vcu
