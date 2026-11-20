"""Scripted drives: what the driver and the rest of the car do over time.

Times are integer milliseconds. The firmware's filters and debounces count
milliseconds, so every boundary here is chosen to sit well clear of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .firmware import HVC_ENERGIZED, HVC_NOT_ENERGIZED, HVC_PRECHARGING


@dataclass(frozen=True)
class Driver:
    accelerator: float = 0.0  # 0..1 of APPS travel
    brake_psi: float = 0.0  # line pressure, front and rear alike
    steering_deg: float = 0.0  # steering wheel
    rtd_pressed: bool = False


@dataclass(frozen=True)
class RestOfCar:
    """The nodes the car model does not include."""
    hvc_state: int = HVC_NOT_ENERGIZED
    contactors_closed: bool = False
    shutdown_loop_v: float = 24.0


@dataclass(frozen=True)
class Launch:
    """Power up, ready to drive on the brake, then full pedal from rest.

    The brake hold is 350 psi: over RTD's 30 psi and clear of the 300 psi
    brake-pressed threshold, where ADC rounding would decide the state. It is
    released 500 ms before the pedal, long after the brake filter has fallen
    under the brake-over-throttle threshold. The RTD switch is a toggle and
    stays on: releasing it leaves DRIVE.
    """
    name: str = 'launch'
    precharge_ms: int = 200
    energized_ms: int = 600
    rtd_ms: int = 1000
    brake_off_ms: int = 1500
    pedal_on_ms: int = 2000
    pedal_off_ms: int = 6000
    duration_ms: int = 7000
    brake_hold_psi: float = 350.0

    def at(self, t_ms: int) -> tuple[Driver, RestOfCar]:
        if t_ms < self.precharge_ms:
            hvc = HVC_NOT_ENERGIZED
        elif t_ms < self.energized_ms:
            hvc = HVC_PRECHARGING
        else:
            hvc = HVC_ENERGIZED
        car = RestOfCar(hvc_state=hvc, contactors_closed=hvc == HVC_ENERGIZED)
        driver = Driver(
            accelerator=1.0 if self.pedal_on_ms <= t_ms < self.pedal_off_ms else 0.0,
            brake_psi=self.brake_hold_psi if t_ms < self.brake_off_ms else 0.0,
            rtd_pressed=t_ms >= self.rtd_ms,
        )
        return driver, car


SCENARIOS = {s.name: s for s in (Launch(),)}
