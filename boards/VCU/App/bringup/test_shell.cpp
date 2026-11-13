#include <cstring>

#include "bringup/test_app.hpp"
#include "bringup/test_helpers.hpp"
#include "longhorn/console.hpp"
#include "longhorn/math.hpp"
#include "longhorn/rtos_lock.hpp"

namespace vcu {

using namespace bringup;  // NOLINT: board-local helpers

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
  shell.SetPlainLineHandler(&longhorn::DfuConsole::PlainLine, nullptr);
}

void TestApp::AdcCommand(void* ctx, longhorn::Console& out, const char*) {
  auto* app = static_cast<TestApp*>(ctx);
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
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
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
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
        longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
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
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  app->shared_.outputs = SafeOutputs();
  app->shared_.tone = ToneRequest{true, true, 0.0f, 0.0f, 0};
  app->shared_.led_override = false;
  out.Println("outputs released to safe state");
}

void TestApp::ToneCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
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
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
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
  app->shared_.led =
      Rgb{longhorn::Clamp(static_cast<float>(v[0]) / 100.0f, 0.0f, 1.0f),
          longhorn::Clamp(static_cast<float>(v[1]) / 100.0f, 0.0f, 1.0f),
          longhorn::Clamp(static_cast<float>(v[2]) / 100.0f, 0.0f, 1.0f)};
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
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
  app->shared_.stream_period_ms = static_cast<uint32_t>(period);
  out.Printf("stream %ld ms (VCP log: counts x7, source mV x7, vdda, din)",
             period);
}

void TestApp::CalCommand(void* ctx, longhorn::Console& out, const char* args) {
  auto* app = static_cast<TestApp*>(ctx);
  longhorn::SchedulerLock lock(app->state_mutex_, pdMS_TO_TICKS(20));
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

}  // namespace vcu
