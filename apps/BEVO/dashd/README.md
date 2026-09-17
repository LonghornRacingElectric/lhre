# dashd

Driver display backend. Subscribes to cand's IPC snapshot stream, serves it
to the kiosk browser over WebSocket (port 8001), and relays pit-to-car
messages from MQTT (contract in [MQTT_CONTRACT.md](MQTT_CONTRACT.md)).
Persists dash layout choices under `/var/lib/bevo-dash/`.

- `frontend/` — the React kiosk UI. Built with `npm run build`, served as
  static files by `deploy/bevo_dash_serve.service`; **not** built by Bazel.
- `deploy/` — Pi kiosk deployment: systemd units, labwc autostart, Plymouth
  splash, `install.sh`/`deploy.sh` runbooks. Nonhermetic by nature.

Config via `DASHD_*` env vars (MQTT host/port — see `main.rs`).
