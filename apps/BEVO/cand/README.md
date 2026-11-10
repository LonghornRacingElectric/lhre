# cand

Artemis acquisition: read CAN, decode with the compiled spec tables, and send
fresh observations to loggerd and publishd. SocketCAN reading, scaling and the
CAN transmit path stay shared with the existing dashboard implementation.

- `:cand` uses SocketCAN on Linux. `CAND_USE_MOCK=1` selects UDP 5005/5006;
  other Unix platforms use that input automatically.
- `:mock_main` runs the same acquisition code with UDP input on every platform.
- `:mock_can` feeds current spec IDs, payload sizes, frame-series IDs and native
  rates. Build `//lib/codegen/telemetry:can_json` first for direct Python use;
  Bazel supplies it automatically. `--can-json` selects an explicit fixture.
- `:run_can_stack` / `:run_mock_can_stack` launch the daemon fleet.

Each CAN frame receives Unix nanoseconds before entering the processing queue.
A decoded update contains only fields supplied by that frame. Repeated fields
wait for all their array slots; incomplete scans expire after their nominal
period. The current cell layouts cover 33 voltage and 23 temperature frames.
CAN has no scan identifier, so completeness is bounded by receipt time.

TCP NMEA observations emit their touched GPS/IMU fields immediately, including
an explicit empty precise-position array when the receiver loses its solution.
Board ages emit once per second. These paths share the compiled Artemis schema.

`/tmp/BEVO_cand_loggerd.sock` and `/tmp/BEVO_cand_publishd.sock` carry
length-prefixed `TelemetryUpdate` messages. Each has an independent bounded
16,384-update queue and writer; overflow is counted and logged. A slow consumer
cannot hold the CAN cache lock. Reconnection can replay an uncertain write.
There is no packet ID or publisher startup gate, and local acquisition does
not depend on MQTT or a reconciliation plan.

The existing `/tmp/BEVO_cand.sock` serves cached `ArtemisSensorData` snapshots
for dashd/debugd at `CAND_PUBLISH_HZ` (default 100). This setting affects only
the dashboard stream. Native history is never produced from that cache.
Interface names and NMEA binding use the existing `CAND_*` settings. CAN schema
tables are compiled; there is no runtime schema override or Orion fallback.
