#include "hvc_app.hpp"

#include <cstring>

namespace hvc {
namespace {

// Parses an "on" / "off" argument. Anything else (including no argument)
// leaves `out` unset and returns false, which the commands treat as
// "just report the current state".
bool ParseOnOff(const char* args, bool* out) {
  if (std::strcmp(args, "on") == 0) {
    *out = true;
    return true;
  }
  if (std::strcmp(args, "off") == 0) {
    *out = false;
    return true;
  }
  return false;
}

// A pin's level as 0/1, or -1 when it isn't wired.
int Level(const lhal::Gpio* pin) {
  return pin == nullptr ? -1 : (pin->Read() ? 1 : 0);
}

// A fixed-point value split for "%s%ld.%ld" printing (no float printf in
// the firmware). Carrying the sign separately keeps -0.5 from printing as
// 0.5.
struct Fixed {
  const char* sign;
  long whole;
  long frac;
};

Fixed Split(int32_t value, int32_t scale) {
  const long mag = value < 0 ? -static_cast<long>(value) : value;
  return {value < 0 ? "-" : "", mag / scale, mag % scale};
}

// mdps -> tenths of a degree per second, rounded.
int32_t MdpsToDecidps(int32_t mdps) {
  return (mdps >= 0 ? mdps + 50 : mdps - 50) / 100;
}

}  // namespace

App::App(const Peripherals& peripherals)
    : p_(peripherals),
      console_mutex_(xSemaphoreCreateMutexStatic(&console_mutex_control_)),
      shell_(p_.console, p_.clock, "HVC", console_mutex_),
      imu_(p_.imu) {
  // Safe defaults regardless of what bring-up left on the pins.
  if (p_.air_minus != nullptr) {
    p_.air_minus->Write(false);
  }
  if (p_.bms_error != nullptr) {
    p_.bms_error->Write(bms_pulse_);
  }
  if (p_.lamp_test != nullptr) {
    p_.lamp_test->Write(true);  // startup light check (HVC-019)
  }
  if (p_.led_red != nullptr && p_.led_green != nullptr &&
      p_.led_blue != nullptr) {
    led_.emplace(p_.led_red, p_.led_green, p_.led_blue);
  }
  imu_mutex_ = xSemaphoreCreateMutexStatic(&imu_mutex_control_);
  imu_.Init();  // retried from Read() if the chip wasn't up yet
  RegisterCommands();
  shell_.PrintBanner();
}

void App::StartTasks() {
  xTaskCreateStatic(&App::LedTaskEntry, "led", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, led_stack_, &led_tcb_);
  xTaskCreateStatic(&App::ShellTaskEntry, "shell", kShellStackDepth, this,
                    tskIDLE_PRIORITY + 1, shell_stack_, &shell_tcb_);
  xTaskCreateStatic(&App::StreamTaskEntry, "stream", kShellStackDepth, this,
                    tskIDLE_PRIORITY + 1, stream_stack_, &stream_tcb_);
  // Above the others: the filter assumes evenly spaced samples.
  xTaskCreateStatic(&App::ImuTaskEntry, "imu", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 2, imu_stack_, &imu_tcb_);
}

void App::LedTaskEntry(void* self) { static_cast<App*>(self)->LedTaskLoop(); }

void App::ShellTaskEntry(void* self) {
  static_cast<App*>(self)->ShellTaskLoop();
}

void App::StreamTaskEntry(void* self) {
  static_cast<App*>(self)->StreamTaskLoop();
}

void App::ImuTaskEntry(void* self) { static_cast<App*>(self)->ImuTaskLoop(); }

void App::ImuTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    UpdateImu();
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kImuPeriodMs));
  }
}

void App::UpdateImu() {
  if (p_.imu == nullptr) {
    return;
  }
  ImuState next = LatestImu();
  next.id_read = imu_.ReadWhoAmI(&next.id);
  ImuSample raw;
  next.has_sample = next.id_read && next.id == Asm330lhb::kWhoAmIValue &&
                    imu_.Read(&raw);
  if (next.has_sample) {
    next.filtered = imu_filter_.Push(raw);
  }
  const bool lock = xTaskGetSchedulerState() == taskSCHEDULER_RUNNING;
  if (lock) {
    xSemaphoreTake(imu_mutex_, portMAX_DELAY);
  }
  imu_state_ = next;
  if (lock) {
    xSemaphoreGive(imu_mutex_);
  }
}

App::ImuState App::LatestImu() {
  const bool lock = xTaskGetSchedulerState() == taskSCHEDULER_RUNNING;
  if (lock) {
    xSemaphoreTake(imu_mutex_, portMAX_DELAY);
  }
  const ImuState copy = imu_state_;
  if (lock) {
    xSemaphoreGive(imu_mutex_);
  }
  return copy;
}

void App::StreamTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    if (stream_) {
      StreamStatus();
    }
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kStreamPeriodMs));
  }
}

void App::StreamStatus() {
  const bool lock = xTaskGetSchedulerState() == taskSCHEDULER_RUNNING;
  if (lock) {
    xSemaphoreTake(console_mutex_, portMAX_DELAY);
  }
  if (kStreamLegs) {
    const Legs legs = ReadLegs();
    auto state = [](bool live) { return live ? "OK" : "BROKEN"; };
    shell_.console().Printf(
        "[%lu ms] SD2 = %s  SD3 = %s  SD4 = %s  SD5 = %s  SD15 = %s",
        static_cast<unsigned long>(p_.clock->Millis()), state(legs.leg2),
        state(legs.leg3), state(legs.leg4), state(legs.leg5),
        state(legs.leg15));
  }

  int32_t code = 0;
  if (p_.pack_sense != nullptr && p_.pack_sense->Read(&code)) {
    const int32_t mv = PackMillivolts(code);
    const int32_t mag = mv < 0 ? -mv : mv;
    shell_.console().Printf("Pack+ = %s%ld.%01ld V  (ADC x16 %ld)",
                            mv < 0 ? "-" : "", static_cast<long>(mag / 1000),
                            static_cast<long>(mag % 1000 / 100),
                            static_cast<long>(code));
  }
  if (p_.current_sense != nullptr && p_.current_sense->Read(&code)) {
    const int32_t ma = CurrentMilliamps(code);
    const int32_t mag = ma < 0 ? -ma : ma;
    shell_.console().Printf("Current = %s%ld.%02ld A  (ADC x16 %ld)",
                            ma < 0 ? "-" : "", static_cast<long>(mag / 1000),
                            static_cast<long>(mag % 1000 / 10),
                            static_cast<long>(code));
  }

  const uint32_t now = p_.clock->Millis();
  if (kStreamBms && p_.bms != nullptr && now >= kBmsProbeStartMs &&
      (!bms_probed_ ||
       lhal::ElapsedMs(now, last_bms_probe_ms_, kBmsProbePeriodMs))) {
    bms_probed_ = true;
    last_bms_probe_ms_ = now;
    CmdBms(shell_.console());
  }
  if (lock) {
    xSemaphoreGive(console_mutex_);
  }
}

void App::LedTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    UpdateLed();
    UpdateBmsErrorPulse();
    UpdateLampTest();
    UpdateAirMinusClose();
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kLedPeriodMs));
  }
}

void App::ShellTaskLoop() {
  for (;;) {
    shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::Step() {
  const uint32_t now = p_.clock->Millis();

  // First iteration: backdate the timer so the first update fires
  // immediately instead of one period after boot.
  if (!started_) {
    started_ = true;
    last_led_ms_ = now - kLedPeriodMs;
    last_stream_ms_ = now - kStreamPeriodMs;
    last_imu_ms_ = now - kImuPeriodMs;
  }

  if (lhal::ElapsedMs(now, last_imu_ms_, kImuPeriodMs)) {
    last_imu_ms_ = now;
    UpdateImu();
  }

  if (lhal::ElapsedMs(now, last_led_ms_, kLedPeriodMs)) {
    last_led_ms_ = now;
    UpdateLed();
    UpdateBmsErrorPulse();
    UpdateLampTest();
    UpdateAirMinusClose();
  }

  if (lhal::ElapsedMs(now, last_stream_ms_, kStreamPeriodMs)) {
    last_stream_ms_ = now;
    if (stream_) {
      StreamStatus();
    }
  }

  // Last, so commands see this step's IMU sample.
  shell_.Poll();
}

void App::UpdateLed() {
  if (led_) {
    led_->Rainbow(kLedPeriodMs / 1000.0f);
    ++led_updates_;
  }
}

void App::UpdateBmsErrorPulse() {
  if (bms_pulse_ && p_.clock->Millis() >= kBmsErrorPulseMs) {
    bms_pulse_ = false;
    if (p_.bms_error != nullptr) {
      p_.bms_error->Write(false);
    }
  }
}

void App::UpdateLampTest() {
  if (lamp_test_ && p_.clock->Millis() >= kLampTestMs) {
    lamp_test_ = false;
    if (p_.lamp_test != nullptr) {
      p_.lamp_test->Write(false);
    }
  }
}

void App::UpdateAirMinusClose() {
  if (air_close_pending_ && p_.clock->Millis() >= kAirMinusCloseMs) {
    air_close_pending_ = false;
    if (p_.air_minus != nullptr) {
      p_.air_minus->Write(true);
    }
  }
}

void App::RegisterCommands() {
  shell_.AddCommand({"legs", "shutdown legs 2/3/4/5/15 + where it's open",
                     [](void* c, longhorn::Console& out, const char*) {
                       static_cast<App*>(c)->CmdLegs(out);
                     },
                     this});
  shell_.AddCommand({"air", "[on|off] AIR- (no checks!) + AIR+/- feedback",
                     [](void* c, longhorn::Console& out, const char* args) {
                       static_cast<App*>(c)->CmdAir(out, args);
                     },
                     this});
  shell_.AddCommand({"bmserr", "[on|off] BMS error output",
                     [](void* c, longhorn::Console& out, const char* args) {
                       static_cast<App*>(c)->CmdBmsError(out, args);
                     },
                     this});
  shell_.AddCommand({"imd", "IMD error input",
                     [](void* c, longhorn::Console& out, const char*) {
                       static_cast<App*>(c)->CmdImd(out);
                     },
                     this});
  shell_.AddCommand({"imu", "IMU device ID + one reading",
                     [](void* c, longhorn::Console& out, const char*) {
                       static_cast<App*>(c)->CmdImu(out);
                     },
                     this});
  shell_.AddCommand({"bms", "BMB serial IDs, configure, read cell voltages",
                     [](void* c, longhorn::Console& out, const char*) {
                       static_cast<App*>(c)->CmdBms(out);
                     },
                     this});
  shell_.AddCommand({"stream", "[on|off] print pack voltage + current every 500 ms",
                     [](void* c, longhorn::Console& out, const char* args) {
                       static_cast<App*>(c)->CmdStream(out, args);
                     },
                     this});
}

Legs App::ReadLegs() const {
  Legs legs;
  legs.leg2 = p_.leg2 != nullptr && p_.leg2->Read();
  legs.leg3 = p_.leg3 != nullptr && p_.leg3->Read();
  legs.leg4 = p_.leg4 != nullptr && p_.leg4->Read();
  legs.leg5 = p_.leg5 != nullptr && p_.leg5->Read();
  legs.leg15 = p_.leg15 != nullptr && p_.leg15->Read();
  return legs;
}

void App::CmdLegs(longhorn::Console& out) {
  const Legs legs = ReadLegs();
  out.Printf("leg2=%d leg3=%d leg4=%d leg5=%d leg15=%d", legs.leg2,
             legs.leg3, legs.leg4, legs.leg5, legs.leg15);
  out.Printf("open at: %s", ChainBreakName(Diagnose(legs)));
  if (Inconsistent(legs)) {
    out.Println("WARNING: a leg reads live downstream of a dead one");
  }
}

void App::CmdAir(longhorn::Console& out, const char* args) {
  bool on = false;
  if (ParseOnOff(args, &on)) {
    if (p_.air_minus == nullptr) {
      out.Println("AIR- output not wired");
      return;
    }
    air_close_pending_ = false;  // manual control from here on
    p_.air_minus->Write(on);
  }
  out.Printf("AIR- cmd=%d  AIR+ fb=%d  AIR- fb=%d", Level(p_.air_minus),
             Level(p_.air_plus_closed), Level(p_.air_minus_closed));
}

void App::CmdBmsError(longhorn::Console& out, const char* args) {
  bool on = false;
  if (ParseOnOff(args, &on)) {
    if (p_.bms_error == nullptr) {
      out.Println("BMS error output not wired");
      return;
    }
    bms_pulse_ = false;  // manual control from here on
    p_.bms_error->Write(on);
  }
  out.Printf("bms_error=%d", Level(p_.bms_error));
}

void App::CmdImd(longhorn::Console& out) {
  out.Printf("imd_error=%d", Level(p_.imd_error));
}

void App::PrintImu(longhorn::Console& out) {
  // Device ID is re-read with every sample: it's the one register that
  // tells a dead bus (0x00 / 0xFF) apart from a live chip.
  const ImuState imu = LatestImu();
  if (!imu.id_read) {
    out.Println("IMU: device ID read failed (SPI error)");
    return;
  }
  if (imu.id != Asm330lhb::kWhoAmIValue || !imu.has_sample) {
    out.Printf("IMU: device ID=0x%02X (expect 0x%02X) %s, no data", imu.id,
               Asm330lhb::kWhoAmIValue,
               imu.id == Asm330lhb::kWhoAmIValue ? "OK" : "WRONG");
    return;
  }
  const uint8_t id = imu.id;
  const ImuSample& s = imu.filtered;
  // One line per sample, low-pass filtered (ImuFilter). Tilt (degrees)
  // comes from gravity on the accelerometer, so it holds steady; the gyro
  // is shown as a rotation rate (deg/s), not integrated into an angle,
  // because its uncalibrated offset would drift it.
  const Tilt tilt = TiltFromAccel(s);
  const Fixed roll = Split(tilt.roll_decideg, 10);
  const Fixed pitch = Split(tilt.pitch_decideg, 10);
  const Fixed gx = Split(MdpsToDecidps(s.gyro_mdps[0]), 10);
  const Fixed gy = Split(MdpsToDecidps(s.gyro_mdps[1]), 10);
  const Fixed gz = Split(MdpsToDecidps(s.gyro_mdps[2]), 10);
  const Fixed temp = Split(s.temp_centi_c, 100);
  out.Printf(
      "ID=0x%02X | roll=%s%ld.%ld pitch=%s%ld.%ld deg | "
      "gyro x=%s%ld.%ld y=%s%ld.%ld z=%s%ld.%ld deg/s | "
      "accel x=%ld y=%ld z=%ld mg | T=%s%ld.%02ld C",
      id, roll.sign, roll.whole, roll.frac, pitch.sign, pitch.whole,
      pitch.frac, gx.sign, gx.whole, gx.frac, gy.sign, gy.whole, gy.frac,
      gz.sign, gz.whole, gz.frac, static_cast<long>(s.accel_mg[0]),
      static_cast<long>(s.accel_mg[1]), static_cast<long>(s.accel_mg[2]),
      temp.sign, temp.whole, temp.frac);
}

void App::CmdImu(longhorn::Console& out) {
  if (p_.imu == nullptr) {
    out.Println("IMU not wired");
    return;
  }
  PrintImu(out);
}

void App::CmdBms(longhorn::Console& out) {
  if (p_.bms == nullptr) {
    out.Println("BMS chain not wired");
    return;
  }
  BmbId ids[kNumBmbs];
  const size_t n = p_.bms->ReadSerialIds(ids, kNumBmbs);
  size_t ok = 0;
  for (size_t i = 0; i < n; ++i) {
    const BmbId& b = ids[i];
    out.Printf("BMB %2u: SID=%02X%02X%02X%02X%02X%02X  cmd_cnt=%u  %s",
               static_cast<unsigned>(i + 1), b.sid[0], b.sid[1], b.sid[2],
               b.sid[3], b.sid[4], b.sid[5],
               static_cast<unsigned>(b.command_counter),
               b.pec_ok ? "PEC OK" : "PEC FAIL");
    if (b.pec_ok) {
      ++ok;
    }
  }
  out.Printf("%u/%u BMBs responded", static_cast<unsigned>(ok),
             static_cast<unsigned>(n));

  p_.bms->Configure();
  p_.bms->ReadCellVoltages();  // the library prints them
}

void App::CmdStream(longhorn::Console& out, const char* args) {
  bool on = false;
  if (ParseOnOff(args, &on)) {
    stream_ = on;
  }
  out.Printf("stream=%d", stream_ ? 1 : 0);
}

}  // namespace hvc
