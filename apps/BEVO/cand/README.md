# cand

CAN daemon — the input side of BEVO. Reads both FDCAN buses via SocketCAN,
decodes frames using the schema in `../nonhermetic/assets/can.json`, merges
NMEA GPS from a TCP/UDP listener, and publishes protobuf
(`OrionSensorData`) snapshots to the other daemons over Unix sockets.

- `:cand` — the real daemon. Real CAN reading is Linux-only (`socketcan`);
  on macOS it compiles to a stub that tells you to use `mock_main`.
  `CAND_USE_MOCK=1` switches the CAN readers to UDP ports 5005/5006 without
  hardware.
- `:mock_main` — full cand replacement that fabricates plausible data;
  works anywhere with Unix sockets.
- `:mock_can` — pure-Python UDP traffic generator feeding
  `CAND_USE_MOCK=1` cand.
- `:run_can_stack` / `:run_mock_can_stack` — launch the whole daemon fleet
  (see the scripts for the wiring).

Config is via `CAND_*` env vars (interface names, publish rate, schema path
— see the top of `main.rs`).
