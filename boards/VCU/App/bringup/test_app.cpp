#include "bringup/test_app.hpp"

#include "bringup/test_helpers.hpp"
#include "can/send.hpp"
#include "control/steering.hpp"
#include "lhre_can_bench.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/math.hpp"
#include "longhorn/rtos_lock.hpp"

namespace vcu {

using lhre::can::BenchCommand;
namespace bench = lhre::can::bench;
namespace msg = lhre::can::vcu;
using namespace bringup;  // NOLINT: board-local helpers

TestApp::TestApp(const Peripherals& peripherals)
    : p_(peripherals),
      params_(ParamsFor(kDefaultEventMode)),
      vcp_mutex_(xSemaphoreCreateMutexStatic(&vcp_mutex_control_)),
      usb_mutex_(xSemaphoreCreateMutexStatic(&usb_mutex_control_)),
      vcp_shell_(p_.debug_uart, p_.clock, "VCU-TEST", vcp_mutex_),
      usb_shell_(p_.usb_uart, p_.clock, "VCU-TEST", usb_mutex_),
      logger_(p_.debug_uart, p_.clock, vcp_mutex_),
      rainbow_(p_.status_led, p_.clock),
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
  usb_dfu_.Attach(usb_shell_, p_.usb_uart);
  vcp_dfu_.AttachByteHook(vcp_shell_);
  vcp_shell_.PrintBanner();
  logger_.Warning("VCU bring-up test image: no vehicle logic");
  logger_.Info("reset cause: %s", lhal::ToString(p_.reset_cause));
}

void TestApp::StartTasks() {
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  rainbow_.Start();
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
  rainbow_.Step(now);
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
  longhorn::SchedulerLock lock(state_mutex_, pdMS_TO_TICKS(5));
  return shared_.outputs;
}

bool TestApp::tone_playing() {
  longhorn::SchedulerLock lock(state_mutex_, pdMS_TO_TICKS(5));
  return shared_.tone_playing;
}

void TestApp::IoTick(uint32_t now) {
  if (!io_started_) {
    io_started_ = true;
    last_diag_ms_ = now - kDiagPeriodMs;
  }
  // Not hard real time: wait a tick for a shell handler to finish.
  longhorn::SchedulerLock lock(state_mutex_, 1);
  if (!lock.locked()) {
    return;
  }
  Shared& s = shared_;
  s.analog = ReadAnalog(p_.adc1, p_.adc2, p_.vrefint_cal, params_.analog);
  s.inputs = ReadDigitalInputs(p_.inputs);
  const AnalogReadings& a = s.analog;
  s.cal.apps1_travel = longhorn::Normalize(
      a.apps1.source_v, params_.apps.apps1_v_at_0, params_.apps.apps1_v_at_100);
  s.cal.apps2_travel = longhorn::Normalize(
      a.apps2.source_v, params_.apps.apps2_v_at_0, params_.apps.apps2_v_at_100);
  s.cal.bse1_psi = longhorn::Normalize(a.bse1.source_v, params_.bse.v_at_0_psi,
                                       params_.bse.v_at_full) *
                   params_.bse.full_scale_psi;
  s.cal.bse2_psi = longhorn::Normalize(a.bse2.source_v, params_.bse.v_at_0_psi,
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

  if (s.led_override)
    rainbow_.Override(s.led.r, s.led.g, s.led.b);
  else
    rainbow_.Auto();

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
  longhorn::RxSlot<bench::BenchEchoRequestCritical> crit_echo;
  longhorn::RxSlot<bench::BenchEchoRequestData> data_echo;
  longhorn::RxSlot<bench::BenchTestCommand> command;
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
  if (bootloader_request_.exchange(false) && p_.board != nullptr) {
    WriteDigitalOutputs(p_.outputs, SafeOutputs());
    p_.board->EnterBootloader();
    return;
  }
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

}  // namespace vcu
