#include "app/serial_report.hpp"

#include <cstdio>
#include <cstdlib>

namespace vcu {
namespace {

void AnalogRow(longhorn::Console& out, const char* name,
               const AnalogReadings::Channel& c) {
  out.Printf("  %-13s %5u    %6d    %7d", name, c.counts,
             static_cast<int>(c.pin_v * 1000),
             static_cast<int>(c.source_v * 1000));
}

// Console printf has no float support, so volts print as V.mmm from integers.
struct Volts {
  explicit Volts(float v) {
    const long mv = static_cast<long>(v * 1000.0f + (v < 0 ? -0.5f : 0.5f));
    const long a = mv < 0 ? -mv : mv;
    std::snprintf(text, sizeof(text), "%s%ld.%03ld", mv < 0 ? "-" : "",
                  a / 1000, a % 1000);
  }
  char text[16];
};

// Console printf has no float support; print Nm to one decimal as integers.
int Tenths(float x) {
  return static_cast<int>(x * 10.0f + (x < 0 ? -0.5f : 0.5f));
}

void CanRow(longhorn::Console& out, const char* name, const lhal::CanStats& s,
            uint32_t drops) {
  out.Printf("  %-8s TEC=%3u REC=%3u off=%d passive=%d off_events=%lu", name,
             s.tx_error_count, s.rx_error_count, s.bus_off, s.error_passive,
             static_cast<unsigned long>(s.bus_off_count));
  out.Printf("           TX dropped=%lu  RX lost=%lu  inbox dropped=%lu",
             static_cast<unsigned long>(s.tx_dropped),
             static_cast<unsigned long>(s.rx_lost),
             static_cast<unsigned long>(drops));
}

}  // namespace

void PrintCompactSerialReport(longhorn::Console& out,
                              const ControlSnapshot& s) {
  const auto& a = s.analog;
  const auto& i = s.inputs;
  const auto& o = s.outputs;
  out.Printf("--- [%lu ms] state=%s ---", static_cast<unsigned long>(s.now_ms),
             lhre::can::ToString(s.state));
  // Sensor-side volts: 5 V scale for APPS, BSE and steering, 24 V scale for
  // the shutdown loop taps.
  const Volts apps1(a.apps1.source_v), apps2(a.apps2.source_v);
  const Volts bse1(a.bse1.source_v), bse2(a.bse2.source_v);
  const Volts steer(a.steering.source_v);
  const Volts sd_in(a.shutdown_in.source_v), sd_out(a.shutdown_out.source_v);
  out.Printf("ADC volts | APPS1=%s APPS2=%s BSE1=%s BSE2=%s", apps1.text,
             apps2.text, bse1.text, bse2.text);
  out.Printf("ADC volts | STEER=%s SD_IN=%s SD_OUT=%s", steer.text, sd_in.text,
             sd_out.text);
  out.Printf("ADC reference | VREFINT=%u counts VDDA=%dmV", a.vrefint_counts,
             static_cast<int>(a.vdda * 1000));
  out.Printf("GPIO IN | B1disc=%d B1ov=%d B2disc=%d B2ov=%d", i.b1_disconnect,
             i.b1_overvoltage, i.b2_disconnect, i.b2_overvoltage);
  out.Printf("        | hard=%d combined=%d confirmed=%d latch=%d",
             i.hard_braking, i.combined_condition, i.fault_confirmed,
             i.latch_state);
  out.Printf("        | LLout=%d motorPwr=%d motorDisc=%d RTD=%d",
             i.linelock_out, i.motor_power, i.motor_disconnect, i.rtd_pressed);
  out.Printf("GPIO OUT | LL=%d amp=%d BMS=%d IMD=%d CritSTB=%d DataSTB=%d",
             o.linelock_ctrl, o.amp_enable, o.bms_led, o.imd_led, o.crit_stb,
             o.data_stb);
  const int cmd = Tenths(s.torque_command);
  out.Printf("TORQUE   | enable=%d cmd=%s%d.%d Nm pedal=%d%% rpm=%ld",
             s.inverter_enable, cmd < 0 ? "-" : "", std::abs(cmd) / 10,
             std::abs(cmd) % 10, static_cast<int>(s.pedals.pedal_travel * 100),
             static_cast<long>(s.motor_rpm));
  out.Printf("AUDIO    | amp=%d playing=%d play_calls=%u last_status=%u (0=ok)",
             o.amp_enable, s.dac_playing, s.dac_play_calls, s.dac_play_status);
}

void PrintSerialReport(longhorn::Console& out, const ControlSnapshot& s,
                       const SerialHealth& h, lhal::ResetCause reset) {
  const auto& p = s.pedals;
  const auto& i = s.inputs;
  const auto& o = s.outputs;
  out.Println("");
  out.Println(
      "======================= VEHICLE CONTROL UNIT =======================");
  out.Printf("  Uptime: %lu ms   Sample age: %lu ms   Reset: %s",
             static_cast<unsigned long>(h.uptime_ms),
             static_cast<unsigned long>(h.uptime_ms - s.now_ms),
             lhal::ToString(reset));
  out.Printf("  state=%-16s Mode: %s", lhre::can::ToString(s.state),
             lhre::can::ToString(s.event_mode));
  out.Printf("  Transition: %s", s.last_transition);
  out.Printf("  ADC: %-5s  Control cycles: %lu  Telemetry cycles: %lu",
             s.adc_fresh ? "OK" : "STALE",
             static_cast<unsigned long>(h.control_beats),
             static_cast<unsigned long>(h.telemetry_beats));

  out.Println(
      "-- ANALOG INPUTS ---------------------------------------------------");
  out.Printf("  VDDA: %d mV (%s)   VREFINT: %u counts",
             static_cast<int>(s.analog.vdda * 1000),
             s.analog.vdda_from_vrefint ? "measured" : "fallback",
             s.analog.vrefint_counts);
  out.Println("  Channel        Counts    Pin mV  Source mV");
  AnalogRow(out, "APPS 1", s.analog.apps1);
  AnalogRow(out, "APPS 2", s.analog.apps2);
  AnalogRow(out, "Brake front", s.analog.bse1);
  AnalogRow(out, "Brake rear", s.analog.bse2);
  AnalogRow(out, "Steering", s.analog.steering);
  AnalogRow(out, "Shutdown in", s.analog.shutdown_in);
  AnalogRow(out, "Shutdown out", s.analog.shutdown_out);

  out.Println(
      "-- PEDALS / STEERING -----------------------------------------------");
  out.Printf(
      "  APPS1: %3d%%  APPS2: %3d%%  Demand: %3d%%  Mismatch: %d  Invalid: %d",
      static_cast<int>(p.apps1_travel * 100),
      static_cast<int>(p.apps2_travel * 100),
      static_cast<int>(p.pedal_travel * 100), p.apps_mismatch,
      p.apps_implausible);
  out.Printf(
      "  Front: %d psi  Rear pre-lock: %d psi  Rear post-lock: %d psi (%s)",
      static_cast<int>(p.bse1_psi), static_cast<int>(p.bse2_psi),
      static_cast<int>(s.bse3_psi), s.bse3_available ? "fresh" : "STALE");
  out.Printf("  Brake pressed: %d  RTD brake: %d  Brake/throttle latch: %d",
             p.brake_pressed, p.brake_for_rtd, p.brake_over_throttle);
  out.Printf(
      "  Steering: %d mdeg  Travel: %d%%  Range fault: %d  Front bias: %d%%",
      static_cast<int>(s.steering.angle_deg * 1000),
      static_cast<int>(s.steering.travel * 100), s.steering.range_fault,
      static_cast<int>(s.brake_bias * 100));

  out.Println(
      "-- DIGITAL I/O / SHUTDOWN ------------------------------------------");
  out.Printf("  RTD pressed: %d  Motor power: %d  Motor disconnect: %d",
             i.rtd_pressed, i.motor_power, i.motor_disconnect);
  out.Printf(
      "  B1 disconnect/overvoltage: %d/%d  B2 disconnect/overvoltage: %d/%d",
      i.b1_disconnect, i.b1_overvoltage, i.b2_disconnect, i.b2_overvoltage);
  out.Printf("  BSPD combined/confirmed/latch: %d/%d/%d  Loop: %s",
             i.combined_condition, i.fault_confirmed, i.latch_state,
             lhre::can::ToString(s.loop));
  out.Printf(
      "  Hard brake HW/FW/mismatch: %d/%d/%d  Brake latch: %d  Clears: %u",
      i.hard_braking, p.firmware_hard_brake, p.hard_brake_mismatch,
      s.brake_flags_latched, s.fault_resets);
  out.Printf("  OUT linelock=%d amp=%d BMS=%d IMD=%d crit_stb=%d data_stb=%d",
             o.linelock_ctrl, o.amp_enable, o.bms_led, o.imd_led, o.crit_stb,
             o.data_stb);
  out.Printf("  Linelock request/readback: %d/%d  Buzzer: %d",
             s.linelock_request, i.linelock_out, s.buzzer);

  out.Println(
      "-- TORQUE / MOTOR / BATTERY -----------------------------------------");
  out.Printf("  Inverter enable: %d  Command: %d mNm  Motor: %ld RPM (%s)",
             s.inverter_enable, static_cast<int>(s.torque_command * 1000),
             static_cast<long>(s.motor_rpm),
             s.motor_speed_valid ? "fresh" : "STALE");
  out.Printf("  Torque mNm: pedal=%d derated=%d power_limited=%d traction=%d",
             static_cast<int>(s.torque.pedal * 1000),
             static_cast<int>(s.torque.derated * 1000),
             static_cast<int>(s.torque.power_limited * 1000),
             static_cast<int>(s.torque.traction * 1000));
  out.Printf("  Power: %ld W  SoE: %d%%  Estimated cell OCV min/max: %d/%d mV",
             static_cast<long>(s.torque.power_w),
             static_cast<int>(s.battery.soe_pct),
             static_cast<int>(s.battery.ocv_min_cell_v * 1000),
             static_cast<int>(s.battery.ocv_max_cell_v * 1000));
  out.Printf(
      "  Energy net/regen: %ld/%ld mWh  Launch armed: %d  Regen current cut: "
      "%d",
      static_cast<long>(s.battery.net_energy_wh * 1000),
      static_cast<long>(s.battery.regen_energy_wh * 1000), s.launch_armed,
      s.regen_current_cut);
  out.Printf("  Wheels: %s  Reference: %d mm/s  Drive/brake slip: %d/%d%%",
             s.wheel_control.valid ? "fresh" : "STALE",
             static_cast<int>(s.wheel_control.reference_m_s * 1000),
             static_cast<int>(s.wheel_control.drive_slip * 100),
             static_cast<int>(s.wheel_control.braking_slip * 100));

  out.Println(
      "-- CAN HEALTH ------------------------------------------------------");
  CanRow(out, "Critical", h.critical, h.critical_inbox_drops);
  CanRow(out, "Data", h.data, h.data_inbox_drops);

  out.Printf("-- FAULTS 0x%08lx  Primary: %s",
             static_cast<unsigned long>(s.faults),
             lhre::can::ToString(s.primary_fault));
  if (s.faults == 0) out.Println("  None");
  for (uint32_t bit = 0; bit < 32; ++bit) {
    if ((s.faults & (1u << bit)) != 0) {
      out.Printf(
          "  %-32s %s",
          lhre::can::ToString(static_cast<lhre::can::VcuFaultCode>(bit + 1)),
          (kTorqueCutFaults & (1u << bit)) ? "[TORQUE CUT]" : "[diagnostic]");
    }
  }
  out.Println(
      "====================================================================");
}

}  // namespace vcu
