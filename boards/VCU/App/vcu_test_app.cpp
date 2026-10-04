#include "vcu_test_app.hpp"

#include <cstdlib>
#include <cstring>

#include "lhre_can_bench.hpp"
#include "lhre_can_vcu.hpp"
#include "lookup.hpp"
#include "rtos_lock.hpp"
#include "steering.hpp"

namespace vcu {
namespace {

using lhre::can::BenchCommand;
namespace bench = lhre::can::bench;
namespace msg = lhre::can::vcu;

int Send(lhal::CanBus* bus, const lhal::CanFrame& frame) {
  return bus != nullptr && lhal::IsOk(bus->Send(frame)) ? 1 : 0;
}

// Output names for /out, in VcuDigitalOutputs bit order.
struct OutputName {
  const char* name;
  bool DigitalOutputs::* field;
};
constexpr OutputName kOutputs[] = {
    {"linelock", &DigitalOutputs::linelock_ctrl},
    {"amp", &DigitalOutputs::amp_enable},
    {"bms", &DigitalOutputs::bms_led},
    {"imd", &DigitalOutputs::imd_led},
    {"crit_stb", &DigitalOutputs::crit_stb},
    {"data_stb", &DigitalOutputs::data_stb},
};

const AnalogReadings::Channel& ChannelAt(const AnalogReadings& a, size_t i) {
  const AnalogReadings::Channel* channels[] = {
      &a.apps1,    &a.apps2,       &a.bse1,        &a.bse2,
      &a.steering, &a.shutdown_in, &a.shutdown_out};
  return *channels[i];
}
constexpr const char* kChannelNames[] = {"apps1", "apps2",   "bse1",    "bse2",
                                         "steer", "shdn_in", "shdn_out"};

int Mv(float v) { return static_cast<int>(v * 1000.0f); }

// Reads "<int> <int> <int>" (missing values stay as given).
int ParseInts(const char* args, long* values, int max_values) {
  int n = 0;
  const char* cursor = args;
  while (n < max_values && *cursor != '\0') {
    char* end = nullptr;
    const long v = std::strtol(cursor, &end, 10);
    if (end == cursor) {
      break;
    }
    values[n++] = v;
    cursor = end;
  }
  return n;
}

}  // namespace

TestApp::TestApp(const Peripherals& peripherals)
    : p_(peripherals),
      params_(ParamsFor(kDefaultEventMode)),
      vcp_mutex_(xSemaphoreCreateMutexStatic(&vcp_mutex_control_)),
      usb_mutex_(xSemaphoreCreateMutexStatic(&usb_mutex_control_)),
      vcp_shell_(p_.debug_uart, p_.clock, "VCU-TEST", vcp_mutex_),
      usb_shell_(p_.usb_uart, p_.clock, "VCU-TEST", usb_mutex_),
      logger_(p_.debug_uart, p_.clock, vcp_mutex_),
      state_mutex_(xSemaphoreCreateMutexStatic(&state_mutex_control_)) {
  shared_.outputs = SafeOutputs();
  WriteDigitalOutputs(p_.outputs, shared_.outputs);
  if (p_.audio_dac != nullptr) {
    p_.audio_dac->Stop(kDacMidScale);
  }
  critical_inbox_.Attach(p_.critical_can);
  data_inbox_.Attach(p_.data_can);
  RegisterShellCommands(vcp_shell_);
  RegisterShellCommands(usb_shell_);
  vcp_shell_.PrintBanner();
  logger_.Warning("VCU bring-up test image: no vehicle logic");
  logger_.Info("reset cause: %s", lhal::ToString(p_.reset_cause));
}

void TestApp::StartTasks() {
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  xTaskCreateStatic(&TestApp::SupervisorTaskEntry, "supervisor",
                    kSupervisorStack, this, tskIDLE_PRIORITY + 6,
                    supervisor_stack_, &supervisor_tcb_);
  xTaskCreateStatic(&TestApp::IoTaskEntry, "io", kIoStack, this,
                    tskIDLE_PRIORITY + 4, io_stack_, &io_tcb_);
  xTaskCreateStatic(&TestApp::VcpShellTaskEntry, "shell_vcp", kShellStack, this,
                    tskIDLE_PRIORITY + 1, vcp_shell_stack_, &vcp_shell_tcb_);
  xTaskCreateStatic(&TestApp::UsbShellTaskEntry, "shell_usb", kShellStack, this,
                    tskIDLE_PRIORITY + 1, usb_shell_stack_, &usb_shell_tcb_);
}

void TestApp::IoTaskEntry(void* self) {
  auto* app = static_cast<TestApp*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->IoTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kIoPeriodMs));
  }
}

void TestApp::SupervisorTaskEntry(void* self) {
  auto* app = static_cast<TestApp*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->SupervisorTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kSupervisorPeriodMs));
  }
}

void TestApp::VcpShellTaskEntry(void* self) {
  auto* app = static_cast<TestApp*>(self);
  for (;;) {
    app->vcp_shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void TestApp::UsbShellTaskEntry(void* self) {
  auto* app = static_cast<TestApp*>(self);
  for (;;) {
    app->usb_shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void TestApp::Step() {
  const uint32_t now = p_.clock->Millis();
  if (!stepped_) {
    stepped_ = true;
    step_io_ms_ = now - kIoPeriodMs;
    step_supervisor_ms_ = now - kSupervisorPeriodMs;
  }
  vcp_shell_.Poll();
  usb_shell_.Poll();
  logger_.DrainOne(0);
  if (lhal::ElapsedMs(now, step_io_ms_, kIoPeriodMs)) {
    step_io_ms_ = now;
    IoTick(now);
  }
  if (lhal::ElapsedMs(now, step_supervisor_ms_, kSupervisorPeriodMs)) {
    step_supervisor_ms_ = now;
    SupervisorTick(now);
  }
}

DigitalOutputs TestApp::outputs() {
  SchedulerLock lock(state_mutex_, pdMS_TO_TICKS(5));
  return shared_.outputs;
}

bool TestApp::tone_playing() {
  SchedulerLock lock(state_mutex_, pdMS_TO_TICKS(5));
  return shared_.tone_playing;
}

void TestApp::IoTick(uint32_t now) {
  if (!io_started_) {
    io_started_ = true;
    last_diag_ms_ = now - kDiagPeriodMs;
  }
  // Not hard real time: wait a tick for a shell handler to finish.
  SchedulerLock lock(state_mutex_, 1);
  if (!lock.locked()) {
    return;
  }
  Shared& s = shared_;
  s.analog = ReadAnalog(p_.adc1, p_.adc2, p_.vrefint_cal, params_.analog);
  s.inputs = ReadDigitalInputs(p_.inputs);
  const AnalogReadings& a = s.analog;
  s.cal.apps1_travel = Normalize(a.apps1.source_v, params_.apps.apps1_v_at_0,
                                 params_.apps.apps1_v_at_100);
  s.cal.apps2_travel = Normalize(a.apps2.source_v, params_.apps.apps2_v_at_0,
                                 params_.apps.apps2_v_at_100);
  s.cal.bse1_psi = Normalize(a.bse1.source_v, params_.bse.v_at_0_psi,
                             params_.bse.v_at_full) *
                   params_.bse.full_scale_psi;
  s.cal.bse2_psi = Normalize(a.bse2.source_v, params_.bse.v_at_0_psi,
                             params_.bse.v_at_full) *
                   params_.bse.full_scale_psi;
  const SteeringOutputs steer =
      ComputeSteering(a.steering.source_v, params_.steering);
  s.cal.steering_angle_deg = steer.angle_deg;
  s.cal.steering_travel = steer.travel;

  if (s.capturing) {
    for (size_t i = 0; i < kAnalogChannels; ++i) {
      const AnalogReadings::Channel& c = ChannelAt(a, i);
      ChannelStats& st = s.stats[i];
      if (s.capture_samples == 0) {
        st = ChannelStats{c.source_v, c.source_v, 0.0, c.counts, c.counts};
      }
      st.min_v = c.source_v < st.min_v ? c.source_v : st.min_v;
      st.max_v = c.source_v > st.max_v ? c.source_v : st.max_v;
      st.min_counts = c.counts < st.min_counts ? c.counts : st.min_counts;
      st.max_counts = c.counts > st.max_counts ? c.counts : st.max_counts;
      st.sum_v += c.source_v;
    }
    ++s.capture_samples;
  }

  HandleCan(now, s);
  UpdateTone(now, s);
  WriteDigitalOutputs(p_.outputs, s.outputs);

  const Rgb rgb = s.led_override
                      ? s.led
                      : StatusColor(lhre::can::VcuState::kInit,
                                    lhre::can::VcuFaultCode::kNone, now);
  if (p_.led_r != nullptr) p_.led_r->SetDuty(rgb.r);
  if (p_.led_g != nullptr) p_.led_g->SetDuty(rgb.g);
  if (p_.led_b != nullptr) p_.led_b->SetDuty(rgb.b);

  // CAN streaming: analog + digital at 100 Hz, health at 10 Hz.
  SendAnalogDiag(p_.data_can, a, s.cal);
  SendDigitalDiag(p_.data_can, s.inputs, s.outputs,
                  lhre::can::ShutdownLoopState::kUnknown, false, false);
  if (lhal::ElapsedMs(now, last_diag_ms_, kDiagPeriodMs)) {
    last_diag_ms_ = now;
    const lhal::CanStats crit = p_.critical_can != nullptr
                                    ? p_.critical_can->stats()
                                    : lhal::CanStats{};
    const lhal::CanStats data =
        p_.data_can != nullptr ? p_.data_can->stats() : lhal::CanStats{};
    SendHealthDiag(p_.data_can, p_.reset_cause, now, crit, data);
  }
  Stream(now, s);
  io_beats_.fetch_add(1);
}

void TestApp::HandleCan(uint32_t now, Shared& s) {
  RxSlot<bench::BenchEchoRequestCritical> crit_echo;
  RxSlot<bench::BenchEchoRequestData> data_echo;
  RxSlot<bench::BenchTestCommand> command;
  lhal::CanFrame frame;
  while (critical_inbox_.Pop(&frame)) {
    if (crit_echo.TryDecode(frame, now)) {
      msg::VcuEchoReplyCritical reply;
      reply.payload = crit_echo.msg.payload;
      echoes_sent_ += Send(p_.critical_can, reply.ToFrame());
    }
  }
  while (data_inbox_.Pop(&frame)) {
    if (data_echo.TryDecode(frame, now)) {
      msg::VcuEchoReplyData reply;
      reply.payload = data_echo.msg.payload;
      echoes_sent_ += Send(p_.data_can, reply.ToFrame());
      continue;
    }
    if (!command.TryDecode(frame, now)) {
      continue;
    }
    const bench::BenchTestCommand& c = command.msg;
    switch (c.command) {
      case BenchCommand::kSetOutputs: {
        const bool mask[] = {
            c.output_mask_linelock_ctrl, c.output_mask_amp_enable,
            c.output_mask_bms_led,       c.output_mask_imd_led,
            c.output_mask_crit_stb,      c.output_mask_data_stb};
        for (size_t i = 0; i < sizeof(mask) / sizeof(mask[0]); ++i) {
          if (mask[i]) {
            s.outputs.*kOutputs[i].field = ((c.output_values >> i) & 1u) != 0;
          }
        }
        break;
      }
      case BenchCommand::kPlayTone:
        s.tone = ToneRequest{true, false, static_cast<float>(c.tone_frequency),
                             c.tone_amplitude(), c.tone_duration};
        break;
      case BenchCommand::kStopTone:
        s.tone = ToneRequest{true, true, 0.0f, 0.0f, 0};
        break;
      case BenchCommand::kWatchdogTest:
        watchdog_test_.store(true);
        logger_.Warning("watchdog test: refresh stopped");
        break;
      case BenchCommand::kReleaseOutputs:
        s.outputs = SafeOutputs();
        s.tone = ToneRequest{true, true, 0.0f, 0.0f, 0};
        break;
      case BenchCommand::kNone:
        break;
    }
  }
}

void TestApp::UpdateTone(uint32_t now, Shared& s) {
  if (s.tone.pending) {
    s.tone.pending = false;
    tone_.Stop();
    if (!s.tone.stop && s.tone.frequency_hz > 0.0f && p_.audio_dac != nullptr) {
      BuildSineTable(sine_, kSineSamples, s.tone.amplitude);
      const auto rate = static_cast<uint32_t>(s.tone.frequency_hz *
                                              static_cast<float>(kSineSamples));
      dac_playing_ = lhal::IsOk(p_.audio_dac->Play(sine_, kSineSamples, rate));
      if (dac_playing_) {
        tone_.Start(now, s.tone.duration_ms);
        s.outputs.amp_enable = true;
        tone_owns_amp_ = true;
      }
    }
  }
  // The local on-time ceiling applies here too. No shutdown-loop gating on
  // the bench image.
  const bool on = tone_.Update(now, true, params_.buzzer.max_on_ms);
  if (!on && dac_playing_ && p_.audio_dac != nullptr) {
    p_.audio_dac->Stop(kDacMidScale);
    dac_playing_ = false;
  }
  // A tone switches the amp on and back off; /out amp still works alone.
  if (!on && tone_owns_amp_) {
    s.outputs.amp_enable = false;
    tone_owns_amp_ = false;
  }
  s.tone_playing = on;
}

void TestApp::Stream(uint32_t now, const Shared& s) {
  if (s.stream_period_ms == 0 ||
      !lhal::ElapsedMs(now, last_stream_ms_, s.stream_period_ms)) {
    return;
  }
  last_stream_ms_ = now;
  const AnalogReadings& a = s.analog;
  const DigitalInputs& i = s.inputs;
  const unsigned bits = (i.b1_disconnect << 0) | (i.b1_overvoltage << 1) |
                        (i.b2_disconnect << 2) | (i.b2_overvoltage << 3) |
                        (i.hard_braking << 4) | (i.combined_condition << 5) |
                        (i.fault_confirmed << 6) | (i.latch_state << 7) |
                        (i.linelock_out << 8) | (i.motor_power << 9) |
                        (i.motor_disconnect << 10) | (i.rtd_pressed << 11);
  logger_.Info(
      "adc %u %u %u %u %u %u %u mv %d %d %d %d %d %d %d vdda %d din 0x%03x",
      a.apps1.counts, a.apps2.counts, a.bse1.counts, a.bse2.counts,
      a.steering.counts, a.shutdown_in.counts, a.shutdown_out.counts,
      Mv(a.apps1.source_v), Mv(a.apps2.source_v), Mv(a.bse1.source_v),
      Mv(a.bse2.source_v), Mv(a.steering.source_v), Mv(a.shutdown_in.source_v),
      Mv(a.shutdown_out.source_v), Mv(a.vdda), bits);
}

void TestApp::SupervisorTick(uint32_t now) {
  if (!watchdog_started_) {
    watchdog_started_ = true;
    io_progress_ms_ = now;
    if (p_.watchdog != nullptr) {
      p_.watchdog->Start(kWatchdogTimeoutMs);
    }
  }
  const uint32_t beats = io_beats_.load();
  if (beats != seen_io_beats_) {
    seen_io_beats_ = beats;
    io_progress_ms_ = now;
  }
  if (watchdog_test_.load() ||
      lhal::ElapsedMs(now, io_progress_ms_, kIoDeadlineMs)) {
    // Let the IWDG fire; outputs go safe first so the readback after the
    // reset (reset cause IWDG) starts from a known state.
    WriteDigitalOutputs(p_.outputs, SafeOutputs());
    return;
  }
  if (p_.watchdog != nullptr) {
    p_.watchdog->Refresh();
  }
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

void TestApp::RegisterShellCommands(longhorn::Shell& shell) {
  shell.AddCommand({"adc", "every analog channel: counts, mV pin/source, cal",
                    &TestApp::AdcCommand, this});
  shell.AddCommand(
      {"io", "digital inputs and outputs", &TestApp::IoCommand, this});
  shell.AddCommand({"out", "out <linelock|amp|bms|imd|crit_stb|data_stb> <0|1>",
                    &TestApp::OutCommand, this});
  shell.AddCommand({"release", "all outputs to safe state, tone off",
                    &TestApp::ReleaseCommand, this});
  shell.AddCommand({"tone", "tone <hz> <amp %> <ms> (ms capped), or tone off",
                    &TestApp::ToneCommand, this});
  shell.AddCommand(
      {"led", "led <r%> <g%> <b%>, or led auto", &TestApp::LedCommand, this});
  shell.AddCommand({"stream", "stream <ms> (0 = off): VCP log of all inputs",
                    &TestApp::StreamCommand, this});
  shell.AddCommand({"cal", "cal start|stop|show: min/max/mean per channel",
                    &TestApp::CalCommand, this});
  shell.AddCommand({"wdtest", "stop feeding the watchdog (expect IWDG reset)",
                    &TestApp::WdTestCommand, this});
  shell.AddCommand({"info", "reset cause, CAN health, inbox drops",
                    &TestApp::InfoCommand, this});
  shell.SetPlainLineHandler(&TestApp::PlainLine, this);
}

void TestApp::AdcCommand(void* ctx, longhorn::Console& out, const char*) {
  auto* app = static_cast<TestApp*>(ctx);
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  const AnalogReadings& a = app->shared_.analog;
  const CalibratedValues& c = app->shared_.cal;
  out.Printf("vdda=%dmV (%s) vrefint=%u", Mv(a.vdda),
             a.vdda_from_vrefint ? "vrefint" : "nominal", a.vrefint_counts);
  for (size_t i = 0; i < kAnalogChannels; ++i) {
    const AnalogReadings::Channel& ch = ChannelAt(a, i);
    out.Printf("%-8s %4u counts %5d mV pin %6d mV source", kChannelNames[i],
               ch.counts, Mv(ch.pin_v), Mv(ch.source_v));
  }
  out.Printf("cal: apps1 %d%% apps2 %d%% bse1 %d psi bse2 %d psi steer %d deg",
             static_cast<int>(c.apps1_travel * 100.0f),
             static_cast<int>(c.apps2_travel * 100.0f),
             static_cast<int>(c.bse1_psi), static_cast<int>(c.bse2_psi),
             static_cast<int>(c.steering_angle_deg));
}

void TestApp::IoCommand(void* ctx, longhorn::Console& out, const char*) {
  auto* app = static_cast<TestApp*>(ctx);
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  const DigitalInputs& i = app->shared_.inputs;
  const DigitalOutputs& o = app->shared_.outputs;
  out.Printf(
      "B1_Disconnect=%d B1_Overvoltage=%d B2_Disconnect=%d "
      "B2_Overvoltage=%d",
      i.b1_disconnect, i.b1_overvoltage, i.b2_disconnect, i.b2_overvoltage);
  out.Printf(
      "hard_braking=%d combined_condition=%d fault_confirmed=%d "
      "latch_state=%d",
      i.hard_braking, i.combined_condition, i.fault_confirmed, i.latch_state);
  out.Printf("linelock_out=%d motor_power=%d motor_disconnect=%d rtd=%d",
             i.linelock_out, i.motor_power, i.motor_disconnect, i.rtd_pressed);
  out.Printf(
      "out: linelock=%d amp=%d bms=%d imd=%d crit_stb=%d data_stb=%d "
      "tone=%d",
      o.linelock_ctrl, o.amp_enable, o.bms_led, o.imd_led, o.crit_stb,
      o.data_stb, app->shared_.tone_playing);
}

void TestApp::OutCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  const char* space = std::strchr(args, ' ');
  if (space != nullptr && (space[1] == '0' || space[1] == '1')) {
    const size_t len = static_cast<size_t>(space - args);
    for (const OutputName& o : kOutputs) {
      if (std::strlen(o.name) == len && std::strncmp(o.name, args, len) == 0) {
        SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
        app->shared_.outputs.*o.field = space[1] == '1';
        out.Printf("%s=%c", o.name, space[1]);
        return;
      }
    }
  }
  out.Println("usage: /out <linelock|amp|bms|imd|crit_stb|data_stb> <0|1>");
}

void TestApp::ReleaseCommand(void* ctx, longhorn::Console& out, const char*) {
  auto* app = static_cast<TestApp*>(ctx);
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  app->shared_.outputs = SafeOutputs();
  app->shared_.tone = ToneRequest{true, true, 0.0f, 0.0f, 0};
  app->shared_.led_override = false;
  out.Println("outputs released to safe state");
}

void TestApp::ToneCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  if (std::strcmp(args, "off") == 0) {
    app->shared_.tone = ToneRequest{true, true, 0.0f, 0.0f, 0};
    out.Println("tone off");
    return;
  }
  long v[3] = {static_cast<long>(app->params_.buzzer.frequency_hz),
               static_cast<long>(app->params_.buzzer.amplitude * 100.0f),
               static_cast<long>(app->params_.buzzer.duration_ms)};
  ParseInts(args, v, 3);
  if (v[0] <= 0 || v[1] < 0 || v[2] <= 0) {
    out.Println("usage: /tone <hz> <amp %> <ms> | /tone off");
    return;
  }
  app->shared_.tone = ToneRequest{true, false, static_cast<float>(v[0]),
                                  static_cast<float>(v[1]) / 100.0f,
                                  static_cast<uint32_t>(v[2])};
  out.Printf("tone %ld Hz %ld%% %ld ms (cap %lu ms)", v[0], v[1], v[2],
             static_cast<unsigned long>(app->params_.buzzer.max_on_ms));
}

void TestApp::LedCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  if (std::strcmp(args, "auto") == 0) {
    app->shared_.led_override = false;
    out.Println("led auto");
    return;
  }
  long v[3] = {0, 0, 0};
  if (ParseInts(args, v, 3) != 3) {
    out.Println("usage: /led <r%> <g%> <b%> | /led auto");
    return;
  }
  app->shared_.led = Rgb{Clamp(static_cast<float>(v[0]) / 100.0f, 0.0f, 1.0f),
                         Clamp(static_cast<float>(v[1]) / 100.0f, 0.0f, 1.0f),
                         Clamp(static_cast<float>(v[2]) / 100.0f, 0.0f, 1.0f)};
  app->shared_.led_override = true;
  out.Printf("led %ld %ld %ld", v[0], v[1], v[2]);
}

void TestApp::StreamCommand(void* ctx, longhorn::Console& out,
                            const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  long period = 0;
  ParseInts(args, &period, 1);
  if (period != 0 && period < static_cast<long>(kIoPeriodMs)) {
    period = kIoPeriodMs;
  }
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  app->shared_.stream_period_ms = static_cast<uint32_t>(period);
  out.Printf("stream %ld ms (VCP log: counts x7, source mV x7, vdda, din)",
             period);
}

void TestApp::CalCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  Shared& s = app->shared_;
  if (std::strcmp(args, "start") == 0) {
    s.capturing = true;
    s.capture_samples = 0;
    out.Println("capturing: sweep each sensor end to end, then /cal stop");
    return;
  }
  if (std::strcmp(args, "stop") == 0) {
    s.capturing = false;
  } else if (std::strcmp(args, "show") != 0) {
    out.Println("usage: /cal start|stop|show");
    return;
  }
  out.Printf("%lu samples (source volts, counts)",
             static_cast<unsigned long>(s.capture_samples));
  if (s.capture_samples == 0) {
    return;
  }
  for (size_t i = 0; i < kAnalogChannels; ++i) {
    const ChannelStats& st = s.stats[i];
    out.Printf("%-8s min %5d mV (%4u) max %5d mV (%4u) mean %5d mV",
               kChannelNames[i], Mv(st.min_v), st.min_counts, Mv(st.max_v),
               st.max_counts,
               Mv(static_cast<float>(st.sum_v /
                                     static_cast<double>(s.capture_samples))));
  }
}

void TestApp::WdTestCommand(void* ctx, longhorn::Console& out, const char*) {
  static_cast<TestApp*>(ctx)->watchdog_test_.store(true);
  out.Println("watchdog refresh stopped; expect a reset with cause iwdg");
}

void TestApp::InfoCommand(void* ctx, longhorn::Console& out, const char*) {
  auto* app = static_cast<TestApp*>(ctx);
  const lhal::CanStats crit = app->p_.critical_can != nullptr
                                  ? app->p_.critical_can->stats()
                                  : lhal::CanStats{};
  const lhal::CanStats data = app->p_.data_can != nullptr
                                  ? app->p_.data_can->stats()
                                  : lhal::CanStats{};
  out.Printf("reset cause: %s", lhal::ToString(app->p_.reset_cause));
  out.Printf("critical: bus_off=%lu tx_drop=%lu rx_lost=%lu tec=%u rec=%u%s",
             static_cast<unsigned long>(crit.bus_off_count),
             static_cast<unsigned long>(crit.tx_dropped),
             static_cast<unsigned long>(crit.rx_lost), crit.tx_error_count,
             crit.rx_error_count, crit.bus_off ? " BUS-OFF" : "");
  out.Printf("data:     bus_off=%lu tx_drop=%lu rx_lost=%lu tec=%u rec=%u%s",
             static_cast<unsigned long>(data.bus_off_count),
             static_cast<unsigned long>(data.tx_dropped),
             static_cast<unsigned long>(data.rx_lost), data.tx_error_count,
             data.rx_error_count, data.bus_off ? " BUS-OFF" : "");
  out.Printf("echoes sent %lu, inbox drops crit %lu data %lu",
             static_cast<unsigned long>(app->echoes_sent_),
             static_cast<unsigned long>(app->critical_inbox_.dropped()),
             static_cast<unsigned long>(app->data_inbox_.dropped()));
}

void TestApp::PlainLine(void* ctx, longhorn::Console& out, const char* line) {
  auto* app = static_cast<TestApp*>(ctx);
  if (std::strcmp(line, "update.") == 0) {
    out.Println("entering bootloader");
    WriteDigitalOutputs(app->p_.outputs, SafeOutputs());
    if (app->p_.board != nullptr) {
      app->p_.board->EnterBootloader();
    }
    return;
  }
  out.Println("commands start with '/' (try /help)");
}

}  // namespace vcu
