#pragma once

#include <cstdint>
#include <optional>

#include "FreeRTOS.h"
#include "task.h"

#include "lhal/lhal.hpp"
#include "longhorn/led.hpp"
#include "asm330lhb.hpp"
#include "bms_chain.hpp"
#include "pack_sense.hpp"
#include "imu_filter.hpp"
#include "longhorn/shell.hpp"
#include "shutdown_sense.hpp"

namespace hvc {

// Everything the application needs from the outside world. On the target
// these are LHAL STM32 adapters wired up in Board/main.cpp; in tests and
// sims they are LHAL host fakes. `clock` is required; anything left null
// is reported as "not wired" by the shell.
struct Peripherals {
  lhal::Clock* clock = nullptr;
  // Debug shell stream: USB-C CDC, or USART3 on the ST-LINK header.
  lhal::Uart* console = nullptr;

  // RGB status LED, one PWM channel per color. All three or none.
  lhal::Pwm* led_red = nullptr;
  lhal::Pwm* led_green = nullptr;
  lhal::Pwm* led_blue = nullptr;

  // Shutdown sense inputs, high = leg live (see shutdown_sense.hpp).
  lhal::Gpio* leg2 = nullptr;
  lhal::Gpio* leg3 = nullptr;
  lhal::Gpio* leg4 = nullptr;
  lhal::Gpio* leg5 = nullptr;
  lhal::Gpio* leg15 = nullptr;

  lhal::Gpio* air_minus = nullptr;         // out: high closes AIR- (MOSFET)
  lhal::Gpio* air_plus_closed = nullptr;   // in: AIR+ feedback
  lhal::Gpio* air_minus_closed = nullptr;  // in: AIR- feedback
  lhal::Gpio* bms_error = nullptr;         // out: high trips the BMS relay
  lhal::Gpio* imd_error = nullptr;         // in: latched IMD fault
  lhal::Gpio* lamp_test = nullptr;  // out: mux select, high = solid light

  // ASM330LHB IMU on SPI4 (CS on PE11).
  RegisterBus* imu = nullptr;
  // ADBMS6830 BMB chain on SPI2 (CS on PB13), through the isoSPI bridge.
  BmsChain* bms = nullptr;
  // Pack+ voltage, differential on ADC2 (PC5 +, PB2 -).
  DiffAdc* pack_sense = nullptr;
  // Pack current (250 uOhm shunt), differential on ADC2 (PA6 +, PA7 -).
  DiffAdc* current_sense = nullptr;
};

// HVC bench firmware: each board function is exposed on its own through
// the debug shell so it can be validated against the test plan before
// anything ties them together. There is deliberately no state machine
// yet, and /air closes AIR- with no interlock checks — bench use only.
//
// LHAL interfaces only — no ST HAL — so it compiles and runs on the host
// unchanged.
//
// Two ways to run it:
//   - StartTasks() + vTaskStartScheduler(): one statically-allocated task
//     per periodic activity. Firmware and :hvc_sim do this.
//   - Step() in a plain loop: no scheduler; timing from `clock`. Kept for
//     deterministic unit tests (:hvc_app_test drives a TestClock).
// Both paths call the same underlying logic.
class App {
 public:
  // 50 Hz animation step: smooth to the eye, negligible CPU.
  static constexpr uint32_t kLedPeriodMs = 20;
  // Shell RX poll period; USB CDC buffers 1 KiB between polls.
  static constexpr uint32_t kShellPollMs = 2;
  // Bench test (HVC-010/012): hold the BMS error output high for this long
  // after boot, then release it, so the latch can be checked to hold the
  // shutdown relay open until reset is pressed. 0 disables the pulse; set
  // it back to 0 after the test. /bmserr takes over at any time.
  static constexpr uint32_t kBmsErrorPulseMs = 5000;
  // Startup light check (HVC-019): lamp test held high this long after
  // boot, then released to the 555 path.
  static constexpr uint32_t kLampTestMs = 3000;
  // Bench test (HVC-014): close AIR- (the second contactor; AIR+ is always
  // closed) this long after boot. No precharge or shutdown checks — safe
  // loads only, never on a live pack. 0 disables; /air takes over.
  static constexpr uint32_t kAirMinusCloseMs = 5000;
  // Shutdown legs are printed to the console this often (HVC-013), so a
  // terminal shows them change live. /stream off pauses it (e.g. to read
  // other commands' output).
  static constexpr uint32_t kStreamPeriodMs = 500;
  // What the stream prints besides pack+ voltage. Off for now: pack
  // voltage only. /legs and /bms still work on demand.
  static constexpr bool kStreamLegs = false;
  static constexpr bool kStreamBms = false;
  // BMS chain probe: from this long after boot, the stream also reads
  // every BMB's serial ID, configures it and reads its cells (same as
  // /bms), once per kBmsProbePeriodMs, so the terminal shows whether the
  // chain answers without typing anything.
  // The delay gives the BMBs and isoSPI bridge time to power up.
  static constexpr uint32_t kBmsProbeStartMs = 5000;
  static constexpr uint32_t kBmsProbePeriodMs = 1000;
  // IMU sample period: ~100 Hz, matching the chip's 104 Hz output rate,
  // so the low-pass filter sees every new sample. The stream and /imu
  // print the latest filtered result instead of touching the bus.
  static constexpr uint32_t kImuPeriodMs = 10;

  explicit App(const Peripherals& peripherals);

  // Creates the app's FreeRTOS tasks (statically allocated — no heap use).
  // Call once, before vTaskStartScheduler(). The App must outlive the
  // scheduler; on the MCU that means static storage in main(), since the
  // Cortex-M port reclaims main()'s stack when the scheduler starts.
  void StartTasks();

  // One iteration of the scheduler-less main loop. Non-blocking.
  void Step();

  uint32_t led_updates() const { return led_updates_; }

 private:
  // In words, and relative to the platform's minimum (the host simulator
  // port needs page-sized pthread stacks; see
  // drivers/freertos/host/FreeRTOSConfig.h).
  static constexpr configSTACK_DEPTH_TYPE kTaskStackDepth =
      2 * configMINIMAL_STACK_SIZE;
  // The shell runs command handlers on its own stack, and /bms holds the
  // whole chain's response buffers there while the ADBMS library runs.
  // The stream task gets the same: its BMS probe runs /bms, and the
  // library's float printf needs the room.
  static constexpr configSTACK_DEPTH_TYPE kShellStackDepth =
      4 * configMINIMAL_STACK_SIZE;

  static void LedTaskEntry(void* self);
  static void ShellTaskEntry(void* self);
  static void StreamTaskEntry(void* self);
  static void ImuTaskEntry(void* self);
  [[noreturn]] void LedTaskLoop();
  [[noreturn]] void ShellTaskLoop();
  [[noreturn]] void StreamTaskLoop();
  [[noreturn]] void ImuTaskLoop();
  void UpdateLed();
  void UpdateBmsErrorPulse();
  void UpdateLampTest();
  void UpdateAirMinusClose();
  // Reads one IMU sample (device ID + data), filters it, and publishes the
  // result. Only the IMU task (or Step) calls this, so SPI4 has one user.
  void UpdateImu();
  // One timestamped line, each leg OK (live) or BROKEN, plus the BMS
  // probe when it's due. Runs under the console mutex, so it never splits
  // a shell response and never shares SPI2 with a /bms typed meanwhile.
  void StreamStatus();
  void PrintImu(longhorn::Console& out);

  // Shell commands, one per board function.
  void RegisterCommands();
  void CmdLegs(longhorn::Console& out);
  void CmdAir(longhorn::Console& out, const char* args);
  void CmdBmsError(longhorn::Console& out, const char* args);
  void CmdImd(longhorn::Console& out);
  void CmdImu(longhorn::Console& out);
  void CmdBms(longhorn::Console& out);
  void CmdStream(longhorn::Console& out, const char* args);

  Legs ReadLegs() const;

  Peripherals p_;
  // Serializes console output between the shell and the leg stream.
  // Declared before shell_, which is constructed with it.
  StaticSemaphore_t console_mutex_control_;
  SemaphoreHandle_t console_mutex_ = nullptr;
  longhorn::Shell shell_;
  std::optional<longhorn::RgbLed> led_;
  Asm330lhb imu_;
  ImuFilter imu_filter_;

  // Latest IMU result, written by UpdateImu and read by the printers.
  struct ImuState {
    bool has_sample = false;  // false until the first successful read
    bool id_read = false;     // false if the ID read itself failed
    uint8_t id = 0;
    ImuSample filtered;
  };
  ImuState LatestImu();
  StaticSemaphore_t imu_mutex_control_;
  SemaphoreHandle_t imu_mutex_ = nullptr;
  ImuState imu_state_;
  uint32_t last_imu_ms_ = 0;
  bool started_ = false;
  uint32_t last_led_ms_ = 0;
  uint32_t led_updates_ = 0;
  // True while the boot BMS error pulse is running.
  bool bms_pulse_ = kBmsErrorPulseMs > 0;
  // True until the startup lamp test ends.
  bool lamp_test_ = true;
  // True until the timed AIR- close fires or /air takes over.
  bool air_close_pending_ = kAirMinusCloseMs > 0;
  bool stream_ = true;
  uint32_t last_stream_ms_ = 0;
  bool bms_probed_ = false;  // false until the first BMS probe
  uint32_t last_bms_probe_ms_ = 0;

  StaticTask_t led_tcb_;
  StackType_t led_stack_[kTaskStackDepth];
  StaticTask_t shell_tcb_;
  StackType_t shell_stack_[kShellStackDepth];
  StaticTask_t stream_tcb_;
  StackType_t stream_stack_[kShellStackDepth];
  StaticTask_t imu_tcb_;
  StackType_t imu_stack_[kTaskStackDepth];
};

}  // namespace hvc
