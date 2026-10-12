"""
Wheel-speed to body-velocity conversion — the "odometry proxy".

Only the rear wheels are used: they are unsteered, so their rolling direction
is the body's x axis and no steering angle enters the conversion. The front
wheel speeds are still carried on the sensor topic because the real car
reports all four (USM, one frame per corner), and slip detection will want
them.
"""


def body_speed(rear_left_rad_s: float, rear_right_rad_s: float,
               wheel_radius_m: float) -> float:
    """Average both rear wheels into a body-frame longitudinal speed."""
    return 0.5 * (rear_left_rad_s + rear_right_rad_s) * wheel_radius_m
