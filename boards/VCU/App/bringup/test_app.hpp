#pragma once

#include <atomic>
#include <cstdint>

#include "FreeRTOS.h"
#include "can/diag_frames.hpp"
#include "config/params.hpp"
#include "hmi/buzzer.hpp"
#include "hmi/status_led.hpp"
#include "io/analog.hpp"
#include "io/digital_io.hpp"
#include "io/peripherals.hpp"
#include "lhal/lhal.hpp"
#include "longhorn/can_inbox.hpp"
#include "longhorn/dfu_console.hpp"
#include "longhorn/logger.hpp"
#include "longhorn/rainbow_task.hpp"
#include "longhorn/shell.hpp"
#include "semphr.h"
#include "task.h"

namespace vcu {

// Bench image: IO, calibration, CAN echo and watchdog tests; no inverter
// commands.
class TestApp {
 public:
  static constexpr uint32_t kIoPeriodMs = 10;
  static constexpr uint32_t kSupervisorPeriodMs = 20;
  static constexpr uint32_t kWatchdogTimeoutMs = 100;
  static constexpr uint32_t kIoDeadlineMs = 100;
  static constexpr uint32_t kShellPollMs = 2;
  static constexpr uint32_t kDiagPeriodMs = 100;
  static constexpr size_t kAnalogChannels = 7;

  explicit TestApp(const Peripherals& peripherals);

  void StartTasks();
  void Step();

  // Test hooks.
  DigitalOutputs outputs();
  bool tone_playing();
  bool watchdog_test_active() const { return watchdog_test_.load(); }
  uint32_t echoes_sent() const { return echoes_sent_; }

 private:
  static constexpr configSTACK_DEPTH_TYPE kIoStack =
      4 * configMINIMAL_STACK_SIZE;
  static constexpr configSTACK_DEPTH_TYPE kSupervisorStack =
      2 * configMINIMAL_STACK_SIZE;
  static constexpr configSTACK_DEPTH_TYPE kShellStack =
      3 * configMINIMAL_STACK_SIZE;
  static constexpr size_t kSineSamples = 32;

  struct ToneRequest {
    bool pending = false;
    bool stop = false;
    float frequency_hz = 0.0f;
    float amplitude = 0.0f;
    uint32_t duration_ms = 0;
  };

  struct ChannelStats {
    float min_v = 0.0f;
    float max_v = 0.0f;
    double sum_v = 0.0;
    uint16_t min_counts = 0;
    uint16_t max_counts = 0;
  };

  // Shared between the io task and shell handlers (state_mutex_).
  struct Shared {
    DigitalOutputs outputs;
    ToneRequest tone;
    Rgb led;
    bool led_override = false;
    uint32_t stream_period_ms = 0;
    bool capturing = false;
    uint32_t capture_samples = 0;
    ChannelStats stats[kAnalogChannels];
    AnalogReadings analog;
    DigitalInputs inputs;
    CalibratedValues cal;
    bool tone_playing = false;
  };

  static void IoTaskEntry(void* self);
  static void SupervisorTaskEntry(void* self);
  static void VcpShellTaskEntry(void* self);
  static void UsbShellTaskEntry(void* self);

  void IoTick(uint32_t now_ms);
  void SupervisorTick(uint32_t now_ms);
  void HandleCan(uint32_t now_ms, Shared& shared);
  void UpdateTone(uint32_t now_ms, Shared& shared);
  void Stream(uint32_t now_ms, const Shared& shared);

  void RegisterShellCommands(longhorn::Shell& shell);
  static void AdcCommand(void* ctx, longhorn::Console& out, const char*);
  static void IoCommand(void* ctx, longhorn::Console& out, const char*);
  static void OutCommand(void* ctx, longhorn::Console& out, const char* args);
  static void ReleaseCommand(void* ctx, longhorn::Console& out, const char*);
  static void ToneCommand(void* ctx, longhorn::Console& out, const char* args);
  static void LedCommand(void* ctx, longhorn::Console& out, const char* args);
  static void StreamCommand(void* ctx, longhorn::Console& out,
                            const char* args);
  static void CalCommand(void* ctx, longhorn::Console& out, const char* args);
  static void WdTestCommand(void* ctx, longhorn::Console& out, const char*);
  static void InfoCommand(void* ctx, longhorn::Console& out, const char*);

  Peripherals p_;
  const Params& params_;

  StaticSemaphore_t vcp_mutex_control_;
  SemaphoreHandle_t vcp_mutex_ = nullptr;
  StaticSemaphore_t usb_mutex_control_;
  SemaphoreHandle_t usb_mutex_ = nullptr;
  longhorn::Shell vcp_shell_;
  longhorn::Shell usb_shell_;
  longhorn::Logger logger_;
  longhorn::RainbowTask rainbow_;
  std::atomic<bool> bootloader_request_{false};
  longhorn::DfuConsole usb_dfu_{&bootloader_request_};
  longhorn::DfuConsole vcp_dfu_{&bootloader_request_};

  StaticSemaphore_t state_mutex_control_;
  SemaphoreHandle_t state_mutex_ = nullptr;
  Shared shared_;

  longhorn::CanInbox critical_inbox_;
  longhorn::CanInbox data_inbox_;
  Buzzer tone_;
  uint16_t sine_[kSineSamples] = {};
  bool dac_playing_ = false;
  bool tone_owns_amp_ = false;
  uint32_t last_diag_ms_ = 0;
  uint32_t last_stream_ms_ = 0;
  bool io_started_ = false;
  uint32_t echoes_sent_ = 0;

  std::atomic<uint32_t> io_beats_{0};
  std::atomic<bool> watchdog_test_{false};
  bool watchdog_started_ = false;
  uint32_t seen_io_beats_ = 0;
  uint32_t io_progress_ms_ = 0;

  bool stepped_ = false;
  uint32_t step_io_ms_ = 0;
  uint32_t step_supervisor_ms_ = 0;

  StaticTask_t io_tcb_;
  StackType_t io_stack_[kIoStack];
  StaticTask_t supervisor_tcb_;
  StackType_t supervisor_stack_[kSupervisorStack];
  StaticTask_t vcp_shell_tcb_;
  StackType_t vcp_shell_stack_[kShellStack];
  StaticTask_t usb_shell_tcb_;
  StackType_t usb_shell_stack_[kShellStack];
};

}  // namespace vcu
