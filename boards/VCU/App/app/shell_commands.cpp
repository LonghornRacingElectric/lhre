#include <cstring>

#include "app/vcu_app.hpp"
#include "longhorn/console.hpp"

namespace vcu {

using lhre::can::VcuFaultCode;

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
  shell.SetPlainLineHandler(&longhorn::DfuConsole::PlainLine, nullptr);
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
