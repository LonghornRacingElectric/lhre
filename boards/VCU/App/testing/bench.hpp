#pragma once

// Host test bench for the VCU apps: LHAL fakes for every peripheral plus
// helpers that play the rest of the car (HVC, inverter, PDU, DUI) on the
// Critical bus. Used by the *_test.cpp files and the sim; host only.

#include <cstdint>
#include <memory>

#include "config/params.hpp"
#include "io/peripherals.hpp"
#include "lhal/host/adc.hpp"
#include "lhal/host/can.hpp"
#include "lhal/host/dac.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "lhal/host/watchdog.hpp"
#include "lhre_can_bench.hpp"
#include "lhre_can_dui.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_inverter.hpp"
#include "lhre_can_pdu.hpp"
#include "lhre_can_pi.hpp"
#include "lhre_can_usm.hpp"
#include "lhre_can_vcu.hpp"

namespace vcu::bench {

class FakeBoard final : public BoardServices {
 public:
  void EnterBootloader() override { ++bootloader_requests; }
  uint32_t LoadFaultLatch() override { return stored; }
  void SaveFaultLatch(uint32_t value) override {
    stored = value;
    ++saves;
  }

  int bootloader_requests = 0;
  uint32_t stored = 0;
  int saves = 0;
};

// Analog setup that makes VDDA come out at exactly 3.3 V from VREFINT.
inline constexpr uint16_t kVrefintCal = 1650;   // counts at 3.0 V
inline constexpr uint16_t kVrefintAt33 = 1500;  // 3.0 * 1650 / 1500 = 3.3
inline constexpr float kVdda = 3.3f;

struct Bench {
  lhal::host::TestClock clock;
  lhal::host::CanNetwork critical_net;
  lhal::host::CanNetwork data_net;
  lhal::host::Can vcu_critical{&critical_net};
  lhal::host::Can vcu_data{&data_net};
  lhal::host::Can car{&critical_net};  // HVC/inverter/PDU/DUI/Pi
  lhal::host::Can logger{&data_net};   // Pi / bench adapter on Data
  lhal::host::Uart vcp;
  lhal::host::Uart usb;
  lhal::host::AdcScan adc1{kAdc1Channels};
  lhal::host::AdcScan adc2{kAdc2Channels};
  lhal::host::WaveformDac dac;
  lhal::host::Watchdog watchdog;
  lhal::host::Pwm led_r, led_g, led_b;
  longhorn::RgbLed status_led{&led_r, &led_g, &led_b};
  lhal::host::Gpio b1_disconnect, b1_overvoltage, b2_disconnect, b2_overvoltage,
      hard_braking, combined_condition, fault_confirmed, latch_state,
      linelock_out, motor_power, motor_disconnect, rtd_switch;
  lhal::host::Gpio linelock_ctrl, amp_shutdown, bms_led, imd_led, crit_stb,
      data_stb;
  FakeBoard board;
  Params params = ParamsFor(kDefaultEventMode);

  Bench() {
    rtd_switch.Write(true);  // pull-up: released
    adc1.Set(kAdc1Vrefint, kVrefintAt33);
    SetPedals(0.5f, 0.5f);
    SetBrakes(0.5f, 0.5f);
    SetSteering(2.5f);
    SetLoop(24.0f, 24.0f);
  }

  Peripherals peripherals() {
    Peripherals p;
    p.clock = &clock;
    p.critical_can = &vcu_critical;
    p.data_can = &vcu_data;
    p.debug_uart = &vcp;
    p.usb_uart = &usb;
    p.adc1 = &adc1;
    p.adc2 = &adc2;
    p.audio_dac = &dac;
    p.watchdog = &watchdog;
    p.led_r = &led_r;
    p.led_g = &led_g;
    p.led_b = &led_b;
    p.status_led = &status_led;
    p.inputs = {&b1_disconnect,   &b1_overvoltage,   &b2_disconnect,
                &b2_overvoltage,  &hard_braking,     &combined_condition,
                &fault_confirmed, &latch_state,      &linelock_out,
                &motor_power,     &motor_disconnect, &rtd_switch};
    p.outputs = {&linelock_ctrl, &amp_shutdown, &bms_led,
                 &imd_led,       &crit_stb,     &data_stb};
    p.board = &board;
    p.reset_cause = lhal::ResetCause::kPowerOn;
    p.vrefint_cal = kVrefintCal;
    return p;
  }

  // Sensor-side volts through the board divider onto the ADC.
  void SetSensor(lhal::host::AdcScan& adc, size_t rank, float source_v,
                 float ratio) {
    adc.SetVolts(rank, source_v * ratio, kVdda);
  }
  void SetPedals(float apps1_v, float apps2_v) {
    const float r = params.analog.sensor_divider_ratio;
    SetSensor(adc1, kAdc1Apps1, apps1_v, r);
    SetSensor(adc1, kAdc1Apps2, apps2_v, r);
  }
  // Both APPS at `travel` of their calibrated span.
  void SetPedalTravel(float travel) {
    const AppsParams& a = params.apps;
    SetPedals(a.apps1_v_at_0 + travel * (a.apps1_v_at_100 - a.apps1_v_at_0),
              a.apps2_v_at_0 + travel * (a.apps2_v_at_100 - a.apps2_v_at_0));
  }
  void SetBrakes(float bse1_v, float bse2_v) {
    const float r = params.analog.sensor_divider_ratio;
    SetSensor(adc1, kAdc1Bse1, bse1_v, r);
    SetSensor(adc1, kAdc1Bse2, bse2_v, r);
  }
  void SetBrakePsi(float psi) {
    const BseParams& b = params.bse;
    const float v =
        b.v_at_0_psi + psi / b.full_scale_psi * (b.v_at_full - b.v_at_0_psi);
    SetBrakes(v, v);
  }
  void SetSteering(float v) {
    SetSensor(adc2, kAdc2Steering, v, params.analog.sensor_divider_ratio);
  }
  void SetLoop(float in_v, float out_v) {
    const float r = params.analog.shutdown_divider_ratio;
    SetSensor(adc2, kAdc2ShutdownIn, in_v, r);
    SetSensor(adc1, kAdc1ShutdownOut, out_v, r);
  }
  void PressRtd(bool pressed) { rtd_switch.Write(!pressed); }

  // The rest of the car
  bool hvc_alive = true;
  lhre::can::HvcState hvc_state = lhre::can::HvcState::kNotEnergized;
  bool contactors = false;
  bool inverter_alive = true;
  bool speed_alive = true;
  bool current_alive = true;
  bool voltage_alive = true;
  bool adc1_alive = true;
  bool adc2_alive = true;
  float dc_bus_voltage = 500.0f;
  float dc_bus_current = 0.0f;
  uint32_t inverter_run_faults = 0;
  bool pdu_alive = true;
  float bse3_psi = 0.0f;
  bool dui_alive = true;
  bool fault_reset = false;
  bool indicators_alive = true;
  bool bms_error = false;
  bool imd_error = false;
  int16_t motor_rpm = 0;
  bool wheel_alive[4] = {};
  float wheel_rad_s[4] = {};

  void SendWheelFrames() {
    lhre::can::usm::AccelerationVectorUnsprungWheelSpeedFl fl;
    lhre::can::usm::AccelerationVectorUnsprungWheelSpeedFr fr;
    lhre::can::usm::AccelerationVectorUnsprungWheelSpeedRl rl;
    lhre::can::usm::AccelerationVectorUnsprungWheelSpeedRr rr;
    fl.set_wheel_speed(wheel_rad_s[0]);
    fr.set_wheel_speed(wheel_rad_s[1]);
    rl.set_wheel_speed(wheel_rad_s[2]);
    rr.set_wheel_speed(wheel_rad_s[3]);
    if (wheel_alive[0]) logger.Send(fl.ToFrame());
    if (wheel_alive[1]) logger.Send(fr.ToFrame());
    if (wheel_alive[2]) logger.Send(rl.ToFrame());
    if (wheel_alive[3]) logger.Send(rr.ToFrame());
  }

  void Energize() {
    hvc_state = lhre::can::HvcState::kEnergized;
    contactors = true;
  }

  // Sends one round of every live node's periodic frames.
  void SendCarFrames() {
    using namespace lhre::can;  // NOLINT
    if (hvc_alive) {
      hvc::HvcContactorStatus s;
      s.hvc_state = hvc_state;
      s.positive_contactor_closed = contactors;
      s.negative_contactor_closed = contactors;
      car.Send(s.ToFrame());
      hvc::HvcCellLimits c;
      c.set_min_cell_voltage(3.9f);
      c.set_max_cell_voltage(3.95f);
      car.Send(c.ToFrame());
    }
    if (indicators_alive) {
      hvc::HvcIndicators i;
      i.bms_error = bms_error;
      i.imd_error = imd_error;
      car.Send(i.ToFrame());
    }
    if (inverter_alive) {
      inverter::InverterFaults f;
      f.run_faults = inverter_run_faults;
      car.Send(f.ToFrame());
      car.Send(inverter::InverterInternalStates{}.ToFrame());
      inverter::InverterHighSpeed hs;
      hs.motor_speed = motor_rpm;
      if (speed_alive) car.Send(hs.ToFrame());
      if (current_alive) {
        inverter::InverterCurrent current;
        current.set_dc_bus_current(dc_bus_current);
        car.Send(current.ToFrame());
      }
      if (voltage_alive) {
        inverter::InverterVoltage voltage;
        voltage.set_dc_bus_voltage(dc_bus_voltage);
        car.Send(voltage.ToFrame());
      }
    }
    if (pdu_alive) {
      pdu::PduBse3 b;
      b.set_brake_pressure(bse3_psi);
      car.Send(b.ToFrame());
    }
    if (dui_alive) {
      dui::DuiDriverInputs d;
      d.fault_reset = fault_reset;
      car.Send(d.ToFrame());
    }
  }

  // Advances `ms` in 1 ms steps, feeding the car's frames every 5 ms.
  template <class AppT>
  void Run(AppT& app, uint32_t ms) {
    for (uint32_t i = 0; i < ms; ++i) {
      if (adc1_alive) adc1.CompleteScan();
      if (adc2_alive) adc2.CompleteScan();
      if (clock.Millis() % 5 == 0) {
        SendCarFrames();
      }
      if (clock.Millis() % 3 == 0) SendWheelFrames();
      app.Step();
      clock.Advance(1);
    }
  }

  // Latest frame with the given ID seen by `node` since the last call.
  template <class Message>
  bool LastFrame(lhal::host::Can& node, Message* out) {
    bool found = false;
    lhal::CanFrame f;
    while (node.Receive(&f)) {
      if (Message::Matches(f.id) && f.len >= Message::kDlc) {
        *out = Message::FromFrame(f);
        found = true;
      }
    }
    return found;
  }
};

}  // namespace vcu::bench
