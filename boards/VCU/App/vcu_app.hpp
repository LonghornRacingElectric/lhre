#pragma once

#include <atomic>
#include <cstdint>

#include "FreeRTOS.h"
#include "analog.hpp"
#include "battery.hpp"
#include "buzzer.hpp"
#include "control_features.hpp"
#include "dfu.hpp"
#include "digital_io.hpp"
#include "faults.hpp"
#include "lhal/lhal.hpp"
#include "lhre_can_types.hpp"
#include "longhorn/debounce.hpp"
#include "longhorn/logger.hpp"
#include "longhorn/shell.hpp"
#include "pedals.hpp"
#include "rainbow_task.hpp"
#include "semphr.h"
#include "shutdown_loop.hpp"
#include "state_machine.hpp"
#include "steering.hpp"
#include "task.h"
#include "torque.hpp"
#include "vcu_config.hpp"
#include "vcu_io.hpp"
#include "vehicle_rx.hpp"

namespace vcu {

// Control owns live state; telemetry and consoles read a mutex-protected copy.
struct ControlSnapshot {
  uint32_t now_ms = 0;
  VcuState state = VcuState::kInit;
  const char* last_transition = "boot";
  uint32_t faults = 0;
  lhre::can::VcuFaultCode primary_fault = lhre::can::VcuFaultCode::kNone;
  lhre::can::EventMode event_mode = kDefaultEventMode;
  AnalogReadings analog;
  PedalOutputs pedals;
  SteeringOutputs steering;
  float brake_bias = 0.0f;
  lhre::can::ShutdownLoopState loop = lhre::can::ShutdownLoopState::kUnknown;
  DigitalInputs inputs;
  DigitalOutputs outputs;
  float motor_rpm = 0.0f;
  bool motor_speed_valid = false;
  bool adc_fresh = false;
  TorqueOutputs torque;
  float torque_command = 0.0f;
  bool inverter_enable = false;
  BatteryOutputs battery;
  bool buzzer = false;
  bool linelock_request = false;
  bool brake_flags_latched = false;
  uint8_t fault_resets = 0;
  float bse3_psi = 0.0f;
  bool bse3_available = false;
  bool launch_armed = false;
  bool regen_current_cut = false;
  WheelControlResult wheel_control;
};

// Vehicle app. StartTasks() runs on target; Step() drives host tests.
class App {
 public:
  static constexpr uint32_t kControlPeriodMs = 3;
  static constexpr uint32_t kTelemetryPeriodMs = 10;
  static constexpr uint32_t kSupervisorPeriodMs = 20;
  // No watchdog on the 2026 VCU; 100 ms puts outputs safe quickly.
  static constexpr uint32_t kWatchdogTimeoutMs = 100;
  // The supervisor withholds the refresh when a task hasn't run within
  // these windows.
  static constexpr uint32_t kControlDeadlineMs = 30;
  static constexpr uint32_t kTelemetryDeadlineMs = 300;
  static constexpr uint32_t kShellPollMs = 2;

  explicit App(const Peripherals& peripherals,
               const Params* initial_params = nullptr);

  // Creates the tasks. Call once, before vTaskStartScheduler(). The App
  // must outlive the scheduler (static storage in main() on the MCU).
  void StartTasks();

  // One scheduler-less iteration: runs whichever ticks are due. Non-blocking.
  void Step();

  // Read-only views for tests and the shell.
  ControlSnapshot snapshot();
  VcuState state() const { return sm_.state(); }
  uint32_t faults() const { return faults_; }
  float torque_command() const { return torque_command_; }
  longhorn::Logger& logger() { return logger_; }
  uint32_t watchdog_refreshes() const { return watchdog_refreshes_; }

 private:
  // Stacks are multiples of the host FreeRTOS port's minimum stack size.
  static constexpr configSTACK_DEPTH_TYPE kControlStack =
      4 * configMINIMAL_STACK_SIZE;
  static constexpr configSTACK_DEPTH_TYPE kTelemetryStack =
      4 * configMINIMAL_STACK_SIZE;
  static constexpr configSTACK_DEPTH_TYPE kSupervisorStack =
      2 * configMINIMAL_STACK_SIZE;
  static constexpr configSTACK_DEPTH_TYPE kShellStack =
      3 * configMINIMAL_STACK_SIZE;
  static constexpr size_t kSineSamples = 32;

  // Accumulate deadlines to avoid drift when periods are not whole control
  // ticks.
  struct RateGate {
    uint32_t next_ms = 0;
    bool started = false;
    bool Due(uint32_t now_ms, uint32_t period_ms) {
      if (!started) {
        started = true;
        next_ms = now_ms;
      }
      if (static_cast<int32_t>(now_ms - next_ms) < 0) {
        return false;
      }
      next_ms += period_ms;
      // Don't burst to catch up after a stall.
      if (static_cast<int32_t>(now_ms - next_ms) >= 0) {
        next_ms = now_ms + period_ms;
      }
      return true;
    }
  };

  static void ControlTaskEntry(void* self);
  static void TelemetryTaskEntry(void* self);
  static void SupervisorTaskEntry(void* self);
  static void VcpShellTaskEntry(void* self);
  static void UsbShellTaskEntry(void* self);
  void PollUsbConsole();

  void ControlTick(uint32_t now_ms);
  void TelemetryTick(uint32_t now_ms);
  void SupervisorTick(uint32_t now_ms);

  // Control-loop stages.
  void DrainCan(uint32_t now_ms);
  void ApplyModeRequests();
  uint32_t ComputeFaults(uint32_t now_ms, bool inverter_fresh,
                         bool inverter_faulted) const;
  void DriveOutputs(uint32_t now_ms);
  void SendCritical(uint32_t now_ms);
  void Publish(uint32_t now_ms);

  void RegisterShellCommands(longhorn::Shell& shell);
  static void StateCommand(void* ctx, longhorn::Console& out, const char*);
  static void FaultsCommand(void* ctx, longhorn::Console& out, const char*);
  static void IoCommand(void* ctx, longhorn::Console& out, const char*);
  static void AnalogCommand(void* ctx, longhorn::Console& out, const char*);
  static void ModeCommand(void* ctx, longhorn::Console& out, const char* args);
  static void LaunchCommand(void* ctx, longhorn::Console& out,
                            const char* args);
  static void PlainLine(void* ctx, longhorn::Console& out, const char* line);
  static bool UsbByte(void* ctx, uint8_t byte);
  static void UsbReceive(void* ctx, const uint8_t* bytes, size_t len);
  static bool VcpByte(void* ctx, uint8_t byte);

  Peripherals p_;

  // Consoles
  StaticSemaphore_t vcp_mutex_control_;
  SemaphoreHandle_t vcp_mutex_ = nullptr;
  StaticSemaphore_t usb_mutex_control_;
  SemaphoreHandle_t usb_mutex_ = nullptr;
  longhorn::Shell vcp_shell_;
  longhorn::Shell usb_shell_;
  longhorn::Logger logger_;
  bool usb_console_connected_ = false;
  uint32_t usb_stream_last_ms_ = 0;
  vcu::RainbowTask rainbow_;
  vcu::DfuCommand usb_dfu_;
  vcu::DfuCommand vcp_dfu_;

  // Control state (control task only)
  const Params* params_;
  longhorn::CanInbox critical_inbox_;
  longhorn::CanInbox data_inbox_;
  VehicleRx rx_;
  WheelRx wheels_;
  PedalProcessor pedals_;
  AdcProgress adc1_progress_;
  AdcProgress adc2_progress_;
  BrakeBias brake_bias_;
  ShutdownLoop shutdown_loop_;
  longhorn::Debouncer b1_disconnect_;
  longhorn::Debouncer b1_overvoltage_;
  longhorn::Debouncer b2_disconnect_;
  longhorn::Debouncer b2_overvoltage_;
  longhorn::Debouncer motor_power_;
  longhorn::Debouncer motor_disconnect_;
  longhorn::EdgeButton rtd_switch_;
  BrakeFlagLatch brake_latch_;
  ErrorLeds error_leds_;
  StateMachine sm_;
  TorquePath torque_path_;
  BatteryEstimator battery_;
  Buzzer buzzer_;
  BrakeRegen regen_;
  LaunchRamp launch_;
  WheelSlipController wheel_control_;
  uint16_t sine_[kSineSamples] = {};
  bool dac_playing_ = false;
  bool prev_fault_reset_ = false;
  uint32_t handled_mode_commands_ = 0;
  int pending_mode_ = -1;  // EventMode waiting to apply outside DRIVE
  uint32_t boot_ms_ = 0;
  uint32_t last_control_ms_ = 0;
  bool control_started_ = false;
  bool fault_clear_sent_ = false;
  uint32_t faults_ = 0;
  float torque_command_ = 0.0f;
  ControlSnapshot live_;
  RateGate status_gate_;
  RateGate faults_gate_;
  RateGate energy_gate_;

  // Shared between tasks
  StaticSemaphore_t snapshot_mutex_control_;
  SemaphoreHandle_t snapshot_mutex_ = nullptr;
  ControlSnapshot published_;
  std::atomic<bool> bootloader_request_{false};
  std::atomic<bool> launch_request_{false};
  std::atomic<bool> bootloader_entered_{false};
  std::atomic<int> shell_mode_request_{-1};
  std::atomic<uint32_t> control_beats_{0};
  std::atomic<uint32_t> telemetry_beats_{0};
  std::atomic<bool> latch_save_pending_{false};
  std::atomic<uint32_t> latch_save_value_{0};

  // Telemetry state
  RateGate diag_fast_gate_;
  RateGate diag_slow_gate_;

  // Supervisor state
  bool watchdog_started_ = false;
  uint32_t seen_control_beats_ = 0;
  uint32_t seen_telemetry_beats_ = 0;
  uint32_t control_progress_ms_ = 0;
  uint32_t telemetry_progress_ms_ = 0;
  uint32_t watchdog_refreshes_ = 0;

  // Scheduler-less timing
  bool stepped_ = false;
  uint32_t step_control_ms_ = 0;
  uint32_t step_telemetry_ms_ = 0;
  uint32_t step_supervisor_ms_ = 0;

  StaticTask_t control_tcb_;
  StackType_t control_stack_[kControlStack];
  StaticTask_t telemetry_tcb_;
  StackType_t telemetry_stack_[kTelemetryStack];
  StaticTask_t supervisor_tcb_;
  StackType_t supervisor_stack_[kSupervisorStack];
  StaticTask_t vcp_shell_tcb_;
  StackType_t vcp_shell_stack_[kShellStack];
  StaticTask_t usb_shell_tcb_;
  StackType_t usb_shell_stack_[kShellStack];
};

}  // namespace vcu
