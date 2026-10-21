#include "vcu_app.hpp"

#include <cstring>

#include "diag_frames.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/console.hpp"
#include "lookup.hpp"
#include "rtos_lock.hpp"
#include "status_led.hpp"

namespace vcu {
namespace {

using lhre::can::EventMode;
using lhre::can::ShutdownLoopState;
using lhre::can::VcuFaultCode;
namespace msg = lhre::can::vcu;

// Representable ranges of the Critical-bus fields (spec bit width x scale).
constexpr float kU16Volts = 6.5535f;     // u16 x 0.0001 V
constexpr float kU16Fraction = 6.5535f;  // u16 x 0.0001
constexpr float kU16Psi = 3276.75f;      // u16 x 0.05 psi
constexpr float kU16Nm = 6553.5f;        // u16 x 0.1 Nm
constexpr float kS16Nm = 3276.7f;        // s16 x 0.1 Nm
constexpr float kS16Deg = 131.068f;      // s16 x 0.004 deg
constexpr float kU32Wh = 4294967.0f;     // u32 x 0.001 Wh

int Send(lhal::CanBus* bus, const lhal::CanFrame& frame) {
  return bus != nullptr && lhal::IsOk(bus->Send(frame)) ? 1 : 0;
}

}  // namespace

App::App(const Peripherals& peripherals, const Params* initial_params)
    : p_(peripherals),
      vcp_mutex_(xSemaphoreCreateMutexStatic(&vcp_mutex_control_)),
      usb_mutex_(xSemaphoreCreateMutexStatic(&usb_mutex_control_)),
      vcp_shell_(p_.debug_uart, p_.clock, "VCU", vcp_mutex_),
      usb_shell_(p_.usb_uart, p_.clock, "VCU", usb_mutex_),
      logger_(p_.debug_uart, p_.clock, vcp_mutex_),
      rainbow_(p_.status_led, p_.clock),
      params_(initial_params != nullptr ? initial_params
                                        : &ParamsFor(kDefaultEventMode)),
      snapshot_mutex_(xSemaphoreCreateMutexStatic(&snapshot_mutex_control_)) {
  // Outputs to their safe state before anything else runs (Board/ already
  // did this at the pin level before constructing the app).
  WriteDigitalOutputs(p_.outputs, SafeOutputs());
  if (p_.audio_dac != nullptr) {
    p_.audio_dac->Stop(kDacMidScale);
  }
  if (p_.board != nullptr) {
    brake_latch_.Restore(p_.board->LoadFaultLatch());
  }
  critical_inbox_.Attach(p_.critical_can);
  data_inbox_.Attach(p_.data_can);
  live_.event_mode = params_->event_mode;
  published_ = live_;

  RegisterShellCommands(vcp_shell_);
  RegisterShellCommands(usb_shell_);
  if (p_.usb_uart == nullptr ||
      !p_.usb_uart->SetRxObserver(&App::UsbReceive, this)) {
    usb_shell_.SetByteHandler(&App::UsbByte, this);
  }
  vcp_shell_.SetByteHandler(&App::VcpByte, this);
  vcp_shell_.PrintBanner();
  logger_.Info("reset cause: %s", lhal::ToString(p_.reset_cause));
  if (brake_latch_.latched()) {
    logger_.Warning("brake comparator latch restored: dash reset required");
  }
}

void App::StartTasks() {
  // Keep watchdog supervision above control and telemetry.
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  rainbow_.Start();
  xTaskCreateStatic(&App::SupervisorTaskEntry, "supervisor", kSupervisorStack,
                    this, tskIDLE_PRIORITY + 6, supervisor_stack_,
                    &supervisor_tcb_);
  xTaskCreateStatic(&App::ControlTaskEntry, "control", kControlStack, this,
                    tskIDLE_PRIORITY + 5, control_stack_, &control_tcb_);
  xTaskCreateStatic(&App::TelemetryTaskEntry, "telemetry", kTelemetryStack,
                    this, tskIDLE_PRIORITY + 3, telemetry_stack_,
                    &telemetry_tcb_);
  xTaskCreateStatic(&App::VcpShellTaskEntry, "shell_vcp", kShellStack, this,
                    tskIDLE_PRIORITY + 1, vcp_shell_stack_, &vcp_shell_tcb_);
  xTaskCreateStatic(&App::UsbShellTaskEntry, "shell_usb", kShellStack, this,
                    tskIDLE_PRIORITY + 1, usb_shell_stack_, &usb_shell_tcb_);
}

void App::ControlTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->ControlTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kControlPeriodMs));
  }
}

void App::TelemetryTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->TelemetryTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kTelemetryPeriodMs));
  }
}

void App::SupervisorTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->SupervisorTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kSupervisorPeriodMs));
  }
}

void App::VcpShellTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  for (;;) {
    app->vcp_shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::UsbShellTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  for (;;) {
    app->usb_shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::Step() {
  const uint32_t now = p_.clock->Millis();
  if (!stepped_) {
    // Backdate so every tick runs on the first call.
    stepped_ = true;
    step_control_ms_ = now - kControlPeriodMs;
    step_telemetry_ms_ = now - kTelemetryPeriodMs;
    step_supervisor_ms_ = now - kSupervisorPeriodMs;
  }
  vcp_shell_.Poll();
  usb_shell_.Poll();
  rainbow_.Step(now);
  logger_.DrainOne(0);
  if (lhal::ElapsedMs(now, step_control_ms_, kControlPeriodMs)) {
    step_control_ms_ = now;
    ControlTick(now);
  }
  if (lhal::ElapsedMs(now, step_telemetry_ms_, kTelemetryPeriodMs)) {
    step_telemetry_ms_ = now;
    TelemetryTick(now);
  }
  if (lhal::ElapsedMs(now, step_supervisor_ms_, kSupervisorPeriodMs)) {
    step_supervisor_ms_ = now;
    SupervisorTick(now);
  }
}

// Control loop

void App::ControlTick(uint32_t now) {
  if (!control_started_) {
    control_started_ = true;
    boot_ms_ = now;
    last_control_ms_ = now;
  }
  const uint32_t dt_ms = now - last_control_ms_;
  last_control_ms_ = now;
  const Params& P = *params_;

  DrainCan(now);

  // Inputs
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

  // Brake comparator soft latch: cleared only by the dash's reset.
  const bool dui_fresh = rx_.dui_inputs.Fresh(now);
  const bool reset_level = dui_fresh && rx_.dui_inputs.msg.fault_reset;
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
  live_.bse3_available = rx_.pdu_bse3.Fresh(now);
  live_.bse3_psi =
      live_.bse3_available ? rx_.pdu_bse3.msg.brake_pressure() : 0.0f;

  // Faults and state
  const bool inverter_fresh = rx_.InverterFresh(now);
  const bool inverter_faulted = rx_.InverterFaulted(now);
  faults_ = ComputeFaults(now, inverter_fresh, inverter_faulted);

  StateInputs si;
  si.bspd_latched = in.latch_state;
  si.hvc_fresh = rx_.hvc_contactors.Fresh(now);
  si.hvc_state = rx_.hvc_contactors.msg.hvc_state;
  si.contactors_closed = rx_.hvc_contactors.msg.positive_contactor_closed &&
                         rx_.hvc_contactors.msg.negative_contactor_closed;
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
  const VcuState state = sm_.state();
  ApplyModeRequests();

  // Torque
  float rpm = 0.0f;
  live_.motor_speed_valid = rx_.MotorRpm(now, &rpm);
  live_.motor_rpm = rpm;
  // Values may be held for diagnostics, but every required feedback frame
  // must be fresh for DRIVE (including the slower 10 Hz current frame).
  const float dc_v = rx_.inverter_voltage.seen()
                         ? rx_.inverter_voltage.msg.dc_bus_voltage()
                         : 0.0f;
  const float dc_a = rx_.inverter_current.seen()
                         ? rx_.inverter_current.msg.dc_bus_current()
                         : 0.0f;
  BatteryInputs bin;
  bin.dc_bus_current = dc_a;
  bin.dc_bus_voltage = dc_v;
  bin.min_cell_v = rx_.hvc_cell_limits.seen()
                       ? rx_.hvc_cell_limits.msg.min_cell_voltage()
                       : 0.0f;
  bin.max_cell_v = rx_.hvc_cell_limits.seen()
                       ? rx_.hvc_cell_limits.msg.max_cell_voltage()
                       : 0.0f;
  bin.dt_ms = dt_ms;
  live_.battery = battery_.Update(bin, P.battery);

  TorqueInputs tin;
  tin.pedal_travel = ped.pedal_travel;
  tin.motor_rpm = rpm;
  tin.motor_speed_valid = live_.motor_speed_valid;
  tin.dc_bus_voltage = dc_v;
  tin.dc_bus_current = dc_a;
  tin.ocv_min_cell_v = live_.battery.ocv_min_cell_v;
  tin.dt_ms = dt_ms;
  tin.speed_sample_ms = rx_.inverter_high_speed.Fresh(now)
                            ? rx_.inverter_high_speed.last_ms
                            : rx_.motor_position.last_ms;
  TractionParams acceleration_limiter = P.traction;
  if (P.wheel_control.enabled) acceleration_limiter.enabled = false;
  live_.torque =
      torque_path_.Update(tin, P.torque, P.power_limit, acceleration_limiter);

  // Torque only in DRIVE with no torque-cut fault and no brake-over-throttle.
  const bool torque_allowed =
      state == VcuState::kDrive && (faults_ & kTorqueCutFaults) == 0 &&
      !ped.brake_over_throttle && !bootloader_entered_.load() &&
      !bootloader_request_.load();
  WheelInputs win;
  win.fresh = wheels_.Fresh(now);
  win.rad_s[0] = wheels_.fl.msg.wheel_speed();
  win.rad_s[1] = wheels_.fr.msg.wheel_speed();
  win.rad_s[2] = wheels_.rl.msg.wheel_speed();
  win.rad_s[3] = wheels_.rr.msg.wheel_speed();
  live_.wheel_control = wheel_control_.Update(
      win, torque_allowed ? live_.torque.traction : 0, dt_ms, P.wheel_control);
  if (launch_request_.exchange(false)) {
    const bool armed =
        launch_.Arm(torque_allowed && live_.wheel_control.valid &&
                        live_.wheel_control.reference_m_s < 0.5f &&
                        P.event_mode == EventMode::kAcceleration,
                    ped.brake_for_rtd, ped.pedal_travel, rpm, P.launch);
    logger_.Info(
        "launch %s",
        armed ? "armed" : "refused (disabled or not stationary/braked)");
  }
  if (P.launch.enabled && !live_.wheel_control.valid) launch_.Abort();
  const float launch_torque =
      launch_.Update(live_.wheel_control.positive_nm, torque_allowed,
                     ped.brake_for_rtd, ped.pedal_travel, rpm, dt_ms, P.launch);
  const float positive =
      P.launch.enabled && !live_.wheel_control.valid ? 0 : launch_torque;
  live_.launch_armed = launch_.armed();

  RegenInputs rin;
  rin.drive = torque_allowed;
  rin.feedback_fresh = inverter_fresh && live_.bse3_available &&
                       live_.wheel_control.valid &&
                       live_.wheel_control.regen_allowed &&
                       rx_.hvc_cell_limits.Fresh(now) && live_.adc_fresh;
  rin.hard_braking = in.hard_braking || ped.firmware_hard_brake;
  rin.linelock_out = in.linelock_out;
  rin.pedal = ped.pedal_travel;
  rin.rear_pre_psi = ped.bse2_psi;
  rin.rear_post_psi = live_.bse3_psi;
  rin.motor_rpm = rpm;
  rin.dc_v = dc_v;
  rin.dc_a = dc_a;
  rin.max_cell_v = rx_.hvc_cell_limits.msg.max_cell_voltage();
  rin.dt_ms = dt_ms;
  const RegenResult regen = regen_.Update(rin, P.regen);
  live_.regen_current_cut = regen.current_cut;
  const bool regen_braking =
      P.regen.enabled && ped.bse2_psi > P.regen.engage_max_psi;
  torque_command_ = 0.0f;
  if (torque_allowed && !regen.current_cut) {
    if (regen.torque_nm < 0.0f) {
      torque_command_ =
          Clamp(regen.torque_nm, -P.inverter.torque_limit_nm, 0.0f);
    } else if (!regen_braking) {
      torque_command_ = Clamp(positive, 0.0f, P.inverter.torque_limit_nm);
    }
  }
  if (!torque_allowed) torque_path_.Reset();
  live_.torque_command = torque_command_;
  live_.inverter_enable =
      state == VcuState::kDrive && (faults_ & kTorqueCutFaults) == 0 &&
      !bootloader_entered_.load() && !bootloader_request_.load();
  live_.linelock_request = torque_allowed && regen.linelock_request;

  if (state == VcuState::kBspdTripped) rainbow_.Override(0.3f, 0, 0.3f);

  DriveOutputs(now);
  SendCritical(now);
  Publish(now);
  control_beats_.fetch_add(1);
}

void App::DrainCan(uint32_t now) {
  lhal::CanFrame frame;
  while (critical_inbox_.Pop(&frame)) {
    rx_.Dispatch(frame, now);
  }
  // The vehicle image ignores bench frames on the Data bus.
  while (data_inbox_.Pop(&frame)) {
    wheels_.Dispatch(frame, now);
  }
}

void App::ApplyModeRequests() {
  if (rx_.mode_command.count != handled_mode_commands_) {
    handled_mode_commands_ = rx_.mode_command.count;
    pending_mode_ = static_cast<int>(rx_.mode_command.msg.event_mode);
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
  if (!rx_.hvc_contactors.Fresh(now)) f |= kFaultHvcTimeout;
  if (!inverter_fresh) f |= kFaultInverterTimeout;
  if (!rx_.pdu_bse3.Fresh(now)) f |= kFaultPduTimeout | kFaultBse3Unavailable;
  if (inverter_faulted) f |= kFaultInverterFault;
  if (live_.inputs.latch_state) f |= kFaultBspdTripped;
  if (ped.hard_brake_mismatch) f |= kFaultHardBrakeMismatch;
  if (!rx_.dui_inputs.Fresh(now)) f |= kFaultDuiTimeout;
  if (live_.inputs.motor_disconnect) f |= kFaultMotorDisconnect;
  if (live_.loop == ShutdownLoopState::kImplausible) {
    f |= kFaultShutdownSenseImplausible;
  }
  StateInputs hvc;
  hvc.hvc_fresh = rx_.hvc_contactors.Fresh(now);
  hvc.hvc_state = rx_.hvc_contactors.msg.hvc_state;
  hvc.contactors_closed = rx_.hvc_contactors.msg.positive_contactor_closed &&
                          rx_.hvc_contactors.msg.negative_contactor_closed;
  if (sm_.state() == VcuState::kDrive && !StateConditions::Energized(hvc)) {
    f |= kFaultContactorsOpen;
  }
  return f;
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
  error_leds_.Update(rx_.hvc_indicators.Fresh(now),
                     rx_.hvc_indicators.msg.bms_error,
                     rx_.hvc_indicators.msg.imd_error);
  out.bms_led = error_leds_.bms_on();
  out.imd_led = error_leds_.imd_on();

  WriteDigitalOutputs(p_.outputs, out);
}

void App::SendCritical(uint32_t now) {
  const Params& P = *params_;
  lhal::CanBus* bus = p_.critical_can;
  if (bus == nullptr) {
    return;
  }
  const PedalOutputs& ped = live_.pedals;
  const AnalogReadings& a = live_.analog;

  // Inverter command every tick (333 Hz). enable=0 outside DRIVE also
  // satisfies the inverter's disable-before-enable lockout.
  msg::InverterTorqueCommand cmd;
  cmd.set_torque_request(ClampPhys(torque_command_, -kS16Nm, kS16Nm));
  cmd.rpm_request = 0;
  cmd.direction = true;
  cmd.enable = live_.inverter_enable;
  cmd.set_torque_limit(ClampPhys(P.inverter.torque_limit_nm, 0.0f, kS16Nm));
  Send(bus, cmd.ToFrame());

  // One-shot inverter fault clear after boot (lhre-2026 vcu_can.c:234-244).
  if (!fault_clear_sent_ &&
      lhal::ElapsedMs(now, boot_ms_, P.inverter.fault_clear_delay_ms)) {
    msg::InverterParameterRequest clear;
    clear.parameter_address = P.inverter.fault_clear_address;
    clear.write = true;
    clear.data = 0;
    fault_clear_sent_ = Send(bus, clear.ToFrame()) == 1;
  }

  // 333 Hz telemetry set, same IDs and layouts as lhre-2026.
  msg::VcuAppsVoltages av;
  av.set_apps1_voltage(ClampPhys(a.apps1.source_v, 0.0f, kU16Volts));
  av.set_apps2_voltage(ClampPhys(a.apps2.source_v, 0.0f, kU16Volts));
  av.set_apps1_travel(ped.apps1_travel);
  av.set_apps2_travel(ped.apps2_travel);
  Send(bus, av.ToFrame());

  msg::VcuAccelPedal ap;
  ap.set_accel_pedal_travel(ClampPhys(ped.pedal_travel, 0.0f, kU16Fraction));
  ap.apps_faults_apps1_disconnect = ped.apps1_disconnect;
  ap.apps_faults_apps2_disconnect = ped.apps2_disconnect;
  ap.apps_faults_apps1_out_of_range = ped.apps1_out_of_range;
  ap.apps_faults_apps2_out_of_range = ped.apps2_out_of_range;
  ap.apps_faults_apps_mismatch = ped.apps_mismatch;
  ap.apps_faults_apps_implausible = ped.apps_implausible;
  Send(bus, ap.ToFrame());

  msg::VcuBrakePedal bp;
  bp.set_brake_light(ped.brake_light);
  Send(bus, bp.ToFrame());

  msg::VcuBseVoltages bv;
  bv.set_bse_front_voltage(ClampPhys(a.bse1.source_v, 0.0f, kU16Volts));
  bv.set_bse_rear_voltage(ClampPhys(a.bse2.source_v, 0.0f, kU16Volts));
  Send(bus, bv.ToFrame());

  msg::VcuBrakes br;
  br.set_brake_pressure_front(ClampPhys(ped.bse1_psi, 0.0f, kU16Psi));
  br.set_brake_pressure_rear_pre_lock(ClampPhys(ped.bse2_psi, 0.0f, kU16Psi));
  br.set_brake_pressure_rear_post_lock(
      ClampPhys(live_.bse3_psi, 0.0f, kU16Psi));
  br.set_brake_bias(ClampPhys(live_.brake_bias, 0.0f, 1.0f));
  br.bse_faults_bse1_disconnect = ped.bse1_disconnect;
  br.bse_faults_bse2_disconnect = ped.bse2_disconnect;
  br.bse_faults_bse1_out_of_range = ped.bse1_out_of_range;
  br.bse_faults_bse2_out_of_range = ped.bse2_out_of_range;
  Send(bus, br.ToFrame());

  msg::VcuSteering st;
  st.set_steering_angle(ClampPhys(live_.steering.angle_deg, -kS16Deg, kS16Deg));
  Send(bus, st.ToFrame());

  msg::VcuDriveState ds;
  ds.prndl_state = sm_.state() == VcuState::kDrive ? 1 : 0;
  ds.stomp_fault = ped.brake_over_throttle;
  ds.ready_to_drive_buzzer = live_.buzzer;
  ds.set_soc_estimate(ClampPhys(live_.battery.soe_pct, 0.0f, 100.0f));
  ds.line_lock_enabled = live_.inputs.linelock_out;
  ds.event_mode = params_->event_mode;
  ds.line_lock_request = live_.linelock_request;
  Send(bus, ds.ToFrame());

  // 100 Hz heartbeat.
  if (status_gate_.Due(
          now, 1000 / static_cast<uint32_t>(msg::VcuStatus::kFrequencyHz))) {
    msg::VcuStatus hb;
    hb.state = sm_.state();
    hb.faults_sensor_loss =
        (faults_ & (kFaultAppsImplausible | kFaultBse1Range | kFaultBse2Range |
                    kFaultBrakeFlagLatched)) != 0;
    hb.set_torque_request(ClampPhys(torque_command_, -300.0f, 300.0f));
    hb.set_steering_angle(
        ClampPhys(live_.steering.angle_deg, -3276.0f, 3276.0f));
    Send(bus, hb.ToFrame());
  }

  // 10 Hz: faults to the dash, energy, torque path.
  if (faults_gate_.Due(
          now, 1000 / static_cast<uint32_t>(msg::VcuFaults::kFrequencyHz))) {
    msg::VcuFaults vf;
    const uint32_t f = faults_;
    vf.active_faults_apps_implausible = (f & kFaultAppsImplausible) != 0;
    vf.active_faults_apps1_range = (f & kFaultApps1Range) != 0;
    vf.active_faults_apps2_range = (f & kFaultApps2Range) != 0;
    vf.active_faults_bse1_range = (f & kFaultBse1Range) != 0;
    vf.active_faults_bse2_range = (f & kFaultBse2Range) != 0;
    vf.active_faults_steering_range = (f & kFaultSteeringRange) != 0;
    vf.active_faults_brake_flag_latched = (f & kFaultBrakeFlagLatched) != 0;
    vf.active_faults_brake_over_throttle = (f & kFaultBrakeOverThrottle) != 0;
    vf.active_faults_shutdown_open = (f & kFaultShutdownOpen) != 0;
    vf.active_faults_hvc_timeout = (f & kFaultHvcTimeout) != 0;
    vf.active_faults_inverter_timeout = (f & kFaultInverterTimeout) != 0;
    vf.active_faults_pdu_timeout = (f & kFaultPduTimeout) != 0;
    vf.active_faults_inverter_fault = (f & kFaultInverterFault) != 0;
    vf.active_faults_bspd_tripped = (f & kFaultBspdTripped) != 0;
    vf.active_faults_hard_brake_mismatch = (f & kFaultHardBrakeMismatch) != 0;
    vf.active_faults_dui_timeout = (f & kFaultDuiTimeout) != 0;
    vf.active_faults_bse3_unavailable = (f & kFaultBse3Unavailable) != 0;
    vf.active_faults_contactors_open = (f & kFaultContactorsOpen) != 0;
    vf.active_faults_motor_disconnect = (f & kFaultMotorDisconnect) != 0;
    vf.active_faults_shutdown_sense_implausible =
        (f & kFaultShutdownSenseImplausible) != 0;
    vf.active_faults_adc_stale = (f & kFaultAdcStale) != 0;
    vf.primary_fault = PrimaryFault(f);
    vf.state = sm_.state();
    vf.brake_flags_latched = brake_latch_.latched();
    vf.fault_resets = brake_latch_.resets();
    Send(bus, vf.ToFrame());
  }
  if (energy_gate_.Due(
          now, 1000 / static_cast<uint32_t>(msg::VcuEnergy::kFrequencyHz))) {
    msg::VcuEnergy en;
    en.set_net_energy(ClampPhys(static_cast<float>(live_.battery.net_energy_wh),
                                0.0f, kU32Wh));
    en.set_regen_energy(ClampPhys(
        static_cast<float>(live_.battery.regen_energy_wh), 0.0f, kU32Wh));
    Send(bus, en.ToFrame());

    msg::VcuTorquePath tp;
    tp.set_torque_lookup(ClampPhys(live_.torque.lookup, 0.0f, kU16Nm));
    tp.set_torque_derated(ClampPhys(live_.torque.derated, 0.0f, kU16Nm));
    tp.set_torque_power_limited(
        ClampPhys(live_.torque.power_limited, 0.0f, kU16Nm));
    tp.set_torque_final(ClampPhys(torque_command_, 0.0f, kU16Nm));
    Send(bus, tp.ToFrame());
  }
}

void App::Publish(uint32_t now) {
  live_.now_ms = now;
  live_.state = sm_.state();
  live_.last_transition = sm_.last_reason();
  live_.faults = faults_;
  live_.primary_fault = PrimaryFault(faults_);
  live_.brake_flags_latched = brake_latch_.latched();
  live_.fault_resets = brake_latch_.resets();
  // Never block the control loop: skip a publish if a reader holds it.
  SchedulerLock lock(snapshot_mutex_, 0);
  if (lock.locked()) {
    published_ = live_;
  }
}

ControlSnapshot App::snapshot() {
  SchedulerLock lock(snapshot_mutex_, pdMS_TO_TICKS(5));
  return lock.locked() ? published_ : ControlSnapshot{};
}

// Telemetry

void App::TelemetryTick(uint32_t now) {
  const ControlSnapshot s = snapshot();

  if (diag_fast_gate_.Due(now, 1000 / static_cast<uint32_t>(
                                          msg::VcuAdcCountsA::kFrequencyHz))) {
    CalibratedValues cal;
    cal.apps1_travel = s.pedals.apps1_travel_unclamped;
    cal.apps2_travel = s.pedals.apps2_travel_unclamped;
    cal.bse1_psi = s.pedals.bse1_psi_unclamped;
    cal.bse2_psi = s.pedals.bse2_psi_unclamped;
    cal.steering_angle_deg = s.steering.angle_deg;
    cal.steering_travel = s.steering.travel;
    SendAnalogDiag(p_.data_can, s.analog, cal);
    SendDigitalDiag(p_.data_can, s.inputs, s.outputs, s.loop,
                    s.pedals.firmware_hard_brake, s.pedals.hard_brake_mismatch);
  }
  if (diag_slow_gate_.Due(
          now, 1000 / static_cast<uint32_t>(msg::VcuDiag::kFrequencyHz))) {
    const lhal::CanStats crit = p_.critical_can != nullptr
                                    ? p_.critical_can->stats()
                                    : lhal::CanStats{};
    const lhal::CanStats data =
        p_.data_can != nullptr ? p_.data_can->stats() : lhal::CanStats{};
    SendHealthDiag(p_.data_can, p_.reset_cause, now, crit, data);
  }

  // Flash writes for the brake latch happen here, off the control loop.
  if (latch_save_pending_.exchange(false) && p_.board != nullptr) {
    p_.board->SaveFaultLatch(latch_save_value_.load());
  }
  telemetry_beats_.fetch_add(1);
}

// Supervisor

void App::SupervisorTick(uint32_t now) {
  // Like last year's DFU helper, updates work without the rest of the car
  // and do not depend on control-task health. Quiesce outputs before reset.
  if (bootloader_request_.exchange(false) && p_.board != nullptr) {
    bootloader_entered_.store(true);
    WriteDigitalOutputs(p_.outputs, SafeOutputs());
    msg::InverterTorqueCommand stop;
    stop.direction = true;
    Send(p_.critical_can, stop.ToFrame());
    p_.board->EnterBootloader();
    return;
  }
  if (!watchdog_started_) {
    watchdog_started_ = true;
    control_progress_ms_ = now;
    telemetry_progress_ms_ = now;
    if (p_.watchdog != nullptr) {
      p_.watchdog->Start(kWatchdogTimeoutMs);
    }
  }
  const uint32_t control = control_beats_.load();
  const uint32_t telemetry = telemetry_beats_.load();
  if (control != seen_control_beats_) {
    seen_control_beats_ = control;
    control_progress_ms_ = now;
  }
  if (telemetry != seen_telemetry_beats_) {
    seen_telemetry_beats_ = telemetry;
    telemetry_progress_ms_ = now;
  }
  const bool healthy =
      !lhal::ElapsedMs(now, control_progress_ms_, kControlDeadlineMs) &&
      !lhal::ElapsedMs(now, telemetry_progress_ms_, kTelemetryDeadlineMs);
  if (healthy) {
    if (p_.watchdog != nullptr) {
      p_.watchdog->Refresh();
    }
    ++watchdog_refreshes_;
  } else {
    // A critical task stalled: outputs safe now, the IWDG resets us within
    // its timeout.
    WriteDigitalOutputs(p_.outputs, SafeOutputs());
  }
}

// Shell

void App::RegisterShellCommands(longhorn::Shell& shell) {
  shell.AddCommand(
      {"state", "state, faults, mode, torque", &App::StateCommand, this});
  shell.AddCommand({"faults", "active fault list", &App::FaultsCommand, this});
  shell.AddCommand(
      {"io", "digital inputs/outputs, loop", &App::IoCommand, this});
  shell.AddCommand(
      {"analog", "ADC counts, volts, calibrated", &App::AnalogCommand, this});
  shell.AddCommand({"mode", "event mode [0-4] (applied outside DRIVE)",
                    &App::ModeCommand, this});
  shell.AddCommand({"launch", "launch arm (opt-in acceleration ramp)",
                    &App::LaunchCommand, this});
  shell.SetPlainLineHandler(&App::PlainLine, this);
}

void App::StateCommand(void* ctx, longhorn::Console& out, const char*) {
  auto* app = static_cast<App*>(ctx);
  const ControlSnapshot s = app->snapshot();
  out.Printf("state=%s (%s) mode=%s primary=%s faults=0x%08lx",
             lhre::can::ToString(s.state), s.last_transition,
             lhre::can::ToString(s.event_mode),
             lhre::can::ToString(s.primary_fault),
             static_cast<unsigned long>(s.faults));
  out.Printf("torque=%d.%01d Nm enable=%d rpm=%ld pedal=%d%% psi=%d bot=%d",
             static_cast<int>(s.torque_command),
             static_cast<int>(s.torque_command * 10) % 10,
             s.inverter_enable ? 1 : 0, static_cast<long>(s.motor_rpm),
             static_cast<int>(s.pedals.pedal_travel * 100.0f),
             static_cast<int>(s.pedals.bse_psi_filtered),
             s.pedals.brake_over_throttle ? 1 : 0);
  out.Printf("reset=%s brake_latch=%d resets=%u soe=%d%% buzzer=%d",
             lhal::ToString(app->p_.reset_cause), s.brake_flags_latched ? 1 : 0,
             s.fault_resets, static_cast<int>(s.battery.soe_pct),
             s.buzzer ? 1 : 0);
  out.Printf("adc_fresh=%d launch_armed=%d regen_current_cut=%d", s.adc_fresh,
             s.launch_armed, s.regen_current_cut);
  out.Printf("wheel_feedback=%d drive_slip=%d%% brake_slip=%d%%",
             s.wheel_control.valid,
             static_cast<int>(s.wheel_control.drive_slip * 100),
             static_cast<int>(s.wheel_control.braking_slip * 100));
}

void App::FaultsCommand(void* ctx, longhorn::Console& out, const char*) {
  const ControlSnapshot s = static_cast<App*>(ctx)->snapshot();
  if (s.faults == 0) {
    out.Println("no active faults");
    return;
  }
  for (uint32_t i = 0; i < 32; ++i) {
    if ((s.faults & (1u << i)) != 0) {
      const auto code = static_cast<VcuFaultCode>(i + 1);
      out.Printf("%-26s%s", lhre::can::ToString(code),
                 (kTorqueCutFaults & (1u << i)) != 0 ? " (torque cut)" : "");
    }
  }
}

void App::IoCommand(void* ctx, longhorn::Console& out, const char*) {
  const ControlSnapshot s = static_cast<App*>(ctx)->snapshot();
  const DigitalInputs& i = s.inputs;
  const DigitalOutputs& o = s.outputs;
  out.Printf(
      "b1_disc=%d b1_ov=%d b2_disc=%d b2_ov=%d hard=%d comb=%d "
      "conf=%d latch=%d",
      i.b1_disconnect, i.b1_overvoltage, i.b2_disconnect, i.b2_overvoltage,
      i.hard_braking, i.combined_condition, i.fault_confirmed, i.latch_state);
  out.Printf("ll_out=%d motor_pwr=%d motor_disc=%d rtd=%d loop=%s fw_hard=%d",
             i.linelock_out, i.motor_power, i.motor_disconnect, i.rtd_pressed,
             lhre::can::ToString(s.loop), s.pedals.firmware_hard_brake);
  out.Printf("out: ll_ctrl=%d amp=%d bms=%d imd=%d crit_stb=%d data_stb=%d",
             o.linelock_ctrl, o.amp_enable, o.bms_led, o.imd_led, o.crit_stb,
             o.data_stb);
}

void App::AnalogCommand(void* ctx, longhorn::Console& out, const char*) {
  const ControlSnapshot s = static_cast<App*>(ctx)->snapshot();
  const AnalogReadings& a = s.analog;
  auto mv = [](float v) { return static_cast<int>(v * 1000.0f); };
  out.Printf("vdda=%dmV (%s) vrefint=%u", mv(a.vdda),
             a.vdda_from_vrefint ? "vrefint" : "nominal", a.vrefint_counts);
  out.Printf("apps1 %4u %4dmV pin %4dmV src  apps2 %4u %4dmV %4dmV",
             a.apps1.counts, mv(a.apps1.pin_v), mv(a.apps1.source_v),
             a.apps2.counts, mv(a.apps2.pin_v), mv(a.apps2.source_v));
  out.Printf("bse1  %4u %4dmV pin %4dmV src  bse2  %4u %4dmV %4dmV",
             a.bse1.counts, mv(a.bse1.pin_v), mv(a.bse1.source_v),
             a.bse2.counts, mv(a.bse2.pin_v), mv(a.bse2.source_v));
  out.Printf("steer %4u %4dmV pin  angle %d deg  shdn in %dmV out %dmV loop",
             a.steering.counts, mv(a.steering.pin_v),
             static_cast<int>(s.steering.angle_deg), mv(a.shutdown_in.source_v),
             mv(a.shutdown_out.source_v));
}

void App::ModeCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<App*>(ctx);
  if (args[0] >= '0' && args[0] <= '4' && args[1] == '\0') {
    app->shell_mode_request_.store(args[0] - '0');
    out.Println("mode request queued (applies outside DRIVE)");
    return;
  }
  out.Printf("mode=%s; usage: /mode 0..4",
             lhre::can::ToString(app->snapshot().event_mode));
}

void App::PlainLine(void* ctx, longhorn::Console& out, const char* line) {
  (void)ctx;
  // tools/dfu sends "update." to reboot into the ROM bootloader. Refused
  // while the tractive system is up.
  if (std::strcmp(line, "update.") == 0) {
    out.Println("entering bootloader");
    return;
  }
  out.Println("commands start with '/' (try /help)");
}

bool App::UsbByte(void* ctx, uint8_t byte) {
  auto* app = static_cast<App*>(ctx);
  const bool match = app->usb_dfu_.Feed(byte);
  if (match) app->bootloader_request_.store(true);
  return match;
}

void App::UsbReceive(void* ctx, const uint8_t* bytes, size_t len) {
  for (size_t i = 0; i < len; ++i) UsbByte(ctx, bytes[i]);
}

bool App::VcpByte(void* ctx, uint8_t byte) {
  auto* app = static_cast<App*>(ctx);
  const bool match = app->vcp_dfu_.Feed(byte);
  if (match) app->bootloader_request_.store(true);
  return match;
}

void App::LaunchCommand(void* ctx, longhorn::Console& out, const char* args) {
  if (std::strcmp(args, "arm") != 0) {
    out.Println("usage: /launch arm");
    return;
  }
  static_cast<App*>(ctx)->launch_request_.store(true);
  out.Println(
      "launch request queued (requires enabled acceleration configuration)");
}

}  // namespace vcu
