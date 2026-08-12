# publishd

Pit uplink. Subscribes to cand's IPC snapshot stream and republishes the
raw protobuf payloads to the pit MQTT broker (topic + host via `PUBLISHD_*`
env vars, see `main.rs`). Touches `/tmp/BEVO_publishd_ready` once connected
so the stack scripts can sequence startup.
