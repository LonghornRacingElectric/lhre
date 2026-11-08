# publishd

Pit uplink. Subscribes to cand's IPC snapshot stream and republishes the
shared protobuf fields to the pit MQTT broker (topic + host via `PUBLISHD_*`
env vars, see `main.rs`). Touches `/tmp/BEVO_publishd_ready` once connected
so the stack scripts can sequence startup.

Set `BEVO_CORE_RELEASE_ROOT` to the physical installed release directory.
The [updater](../updater/README.md) writes `reconciliation.json`, binding the
intersection to that release's metadata and the deployed server schema.
`publishd` validates the producer identity against its compiled schema and
loads the plan before connecting. Missing/mismatched plans stop startup;
malformed snapshots are dropped. There is no automatic unfiltered mode.

Bazel generates field-copy code from the descriptor and textproto contract;
no sensor-number table is maintained in this daemon. Re-encoding a fresh
message excludes incompatible and unknown fields, including unknown enum
values. Ordered repeated enum fields are omitted as a whole if any element
is outside the shared values, preserving indices. Local logs and other IPC
consumers keep the full snapshot. Implicit proto3 scalar defaults remain
ambiguous at the server; exclusion does not mean database NULL.

Publish rate: `PUBLISHD_PUBLISH_HZ`, default `DEFAULT_PUBLISH_HZ` in `main.rs`
(50 Hz for the GPS experiment, was 20). On the car a systemd drop-in
(`bevo_telemetry.service.d/20-publish-hz.conf`) sets the env var and wins over
the default, so changing the constant alone does not change a deployed car.
