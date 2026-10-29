# publishd

Pit uplink. Subscribes to cand's IPC snapshot stream and republishes the
raw protobuf payloads to the pit MQTT broker (topic + host via `PUBLISHD_*`
env vars, see `main.rs`). Touches `/tmp/BEVO_publishd_ready` once connected
so the stack scripts can sequence startup.

Publish rate: `PUBLISHD_PUBLISH_HZ`, default `DEFAULT_PUBLISH_HZ` in `main.rs`
(50 Hz for the GPS experiment, was 20). On the car a systemd drop-in
(`bevo_telemetry.service.d/20-publish-hz.conf`) sets the env var and wins over
the default, so changing the constant alone does not change a deployed car.
