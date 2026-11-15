# FMU runtime

`plant.py` owns the FMI instance, verifies SI interfaces and artifact identity,
and maps driver commands. It releases the instance on a rejected step and
keeps simulation time at the last accepted step.

`sim_node.py` adapts that plant to the shared ROS clock/odometry/TF interface.
It applies command expiry and publishes the build manifest. It does not
implement another vehicle model. See the [package guide](../README.md) for
builds, parameters and limitations.
