from vcusil.firmware import HVC_ENERGIZED, HVC_NOT_ENERGIZED, HVC_PRECHARGING
from vcusil.scenario import SCENARIOS, Launch

# Firmware thresholds the scenario must stay clear of (config/params.hpp).
PRESSED_ON_PSI = 300.0
PRESSED_OFF_PSI = 250.0
RTD_BRAKE_PSI = 30.0
RTD_DEBOUNCE_MS = 20


def test_launch_is_registered():
    assert isinstance(SCENARIOS['launch'], Launch)


def test_power_up_sequence():
    s = Launch()
    assert s.at(0)[1].hvc_state == HVC_NOT_ENERGIZED
    assert s.at(s.precharge_ms - 1)[1].hvc_state == HVC_NOT_ENERGIZED
    assert s.at(s.precharge_ms)[1].hvc_state == HVC_PRECHARGING
    assert s.at(s.energized_ms - 1)[1].hvc_state == HVC_PRECHARGING
    car = s.at(s.energized_ms)[1]
    assert car.hvc_state == HVC_ENERGIZED and car.contactors_closed
    assert not s.at(s.energized_ms - 1)[1].contactors_closed
    assert all(s.at(t)[1].shutdown_loop_v == 24.0 for t in range(0, s.duration_ms, 3))


def test_driver_timeline_respects_the_firmware():
    s = Launch()
    # RTD on the brake, after the HVC is energized, and held to the end.
    assert s.rtd_ms > s.energized_ms + RTD_DEBOUNCE_MS
    assert s.at(s.rtd_ms)[0].brake_psi >= RTD_BRAKE_PSI
    assert all(s.at(t)[0].rtd_pressed for t in range(s.rtd_ms, s.duration_ms))
    assert not any(s.at(t)[0].rtd_pressed for t in range(0, s.rtd_ms))
    # Off the brake long before the pedal (its filter must fall under the
    # brake-over-throttle threshold), and never both at once.
    assert s.pedal_on_ms - s.brake_off_ms >= 300
    for t in range(s.duration_ms):
        d = s.at(t)[0]
        assert not (d.accelerator > 0 and d.brake_psi > 0), t
    # Clear of the brake-pressed hysteresis, where ADC rounding decides.
    assert abs(s.brake_hold_psi - PRESSED_ON_PSI) > 25
    assert abs(s.brake_hold_psi - PRESSED_OFF_PSI) > 25
    assert s.at(s.pedal_on_ms - 1)[0].accelerator == 0.0
    assert s.at(s.pedal_on_ms)[0].accelerator == 1.0
    assert s.at(s.pedal_off_ms - 1)[0].accelerator == 1.0
    assert s.at(s.pedal_off_ms)[0].accelerator == 0.0
