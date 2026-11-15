# Two-laptop CAN demo

Run BEVO in **Linux ARM64**, natively or in a VM. Join that Linux environment
and the server laptop to the same Tailnet and synchronize their clocks.

Generated CAN → vcan → actual cand → publishd → Tailnet MQTT → ingest/bridge →
Kafka → writer/Timescale → gateway → Grafana and Trackside. Loggerd writes local
frequency-pool CSVs alongside the uplink. The real updater installs the core
from GHCR; the BEVO laptop needs no compiler or source checkout.

Virtual CAN tests software, not electrical arbitration or bus capacity. The
laptop launcher uses existing network connectivity instead of GPIO/modem setup.
GPS/map simulation and the driver kiosk are outside this demo. Signal values
are synthetic ten-second ramps bounded by their CAN encodings. No board
dropouts are injected.

## Server preparation

Use a reviewed revision for both server images and the BEVO release. Publish
explicitly through the existing manual workflow, then build the support bundle:

```bash
gh workflow run ci.yml --ref YOUR_REVIEWED_REF -f publish=true
bazel build --config=local //apps/BEVO:demo_bundle
```

Wait for publication and record its immutable
`ghcr.io/longhornracingelectric/bevo-core@sha256:...` reference. Reuse an already
published matching release when available. Tags alone are not accepted.

Configure the server's root `.env` using the
[Grafana setup](../../telemetry/stack/kafka/grafana/README.md), preserving existing
passwords and volumes. Set `QUERY_API_TOKEN`, `QUERY_CURSOR_SECRET` and local
Grafana URL settings. From the repository root:

```bash
./apps/telemetry/stack/server_devtool.sh build kafka ingest field_enricher board_health
```

Start the viewer through its
[local setup](../../telemetry/analysis/database/viewer_tool/README.md#local-setup).
It uses the same server-side API token; sign in normally.

Approve the BEVO digest in a local file. This uses the existing manual release
list rather than automatic catalog/channel promotion:

```bash
export BEVO_RELEASE='ghcr.io/longhornracingelectric/bevo-core@sha256:REPLACE_WITH_DIGEST'
printf '{"releases":["%s"]}\n' "$BEVO_RELEASE" > /tmp/bevo-demo-releases.json
./apps/telemetry/stack/server_devtool.sh schema-serve --config /tmp/bevo-demo-releases.json
```

Keep that terminal open. The authority reads matching metadata from the running
ingest and bridge images. In another terminal:

```bash
tailscale status
tailscale serve status
tailscale serve --bg --https=8443 http://127.0.0.1:8085
```

Reserve 8443 for this demo; do not replace an existing Serve endpoint. Use the
printed HTTPS hostname with `/profile.json` appended. The sibling
`/schema_descriptor.pb` must also be reachable. Serve requires Tailnet HTTPS
enabled. Access rules must permit BEVO → server on **8443** and **1883**.
The query gateway stays on loopback; clients run on the server. No Funnel is used.

## BEVO preparation and startup

Copy `bazel-bin/apps/BEVO/demo_bundle.tar.gz` into Linux ARM64 and extract it:

```bash
mkdir -p "$HOME/bevo-demo"
tar xzf demo_bundle.tar.gz -C "$HOME/bevo-demo"
cd "$HOME/bevo-demo"
uname -m                    # aarch64
tailscale status
```

Linux needs Python 3.9+, Bash, iproute2 and a kernel with `vcan`. Ubuntu/Debian
can install the user tools with `sudo apt-get install python3 iproute2`.
Configure two virtual buses, then run the launcher as your normal user:

```bash
sudo modprobe vcan
ip link show vcan0 >/dev/null 2>&1 || sudo ip link add dev vcan0 type vcan
ip link show vcan1 >/dev/null 2>&1 || sudo ip link add dev vcan1 type vcan
sudo ip link set vcan0 up
sudo ip link set vcan1 up

export BEVO_SERVER_URL='https://SERVER.YOUR-TAILNET.ts.net:8443/profile.json'
export PUBLISHD_MQTT_HOST='SERVER_TAILNET_IP'
export PUBLISHD_MQTT_PORT=1883
export PUBLISHD_MQTT_CLIENT_ID=bevo-laptop-demo
export BEVO_CORE_ROOT="$HOME/.local/share/bevo-demo/core"
export LOGGERD_LOG_DIR="$HOME/.local/share/bevo-demo/logs"
python3 -c 'import os,urllib.request; print(urllib.request.urlopen(os.environ["BEVO_SERVER_URL"]).status)'
bash nonhermetic/run_demo_stack.sh 300
```

Private GHCR packages also need `BEVO_REGISTRY_USER` and `BEVO_REGISTRY_TOKEN`
with read access on this laptop. A fresh root prints `install`, its selected
physical activation and the CSV directory. All three daemons use that activation.
The generator reads its `can.json`: Critical frames use `vcan0`, Data Acq frames
use `vcan1`, at generated native rates. Publishd retains its normal reconciliation
and uplink limits. Five minutes is the default; Ctrl+C stops children and flushes logs.

`BEVO_NETWORK_WAIT_S` defaults to 60 seconds for the **entire** startup sync,
including download. Failed first installs stop startup; fix connectivity and
retry. A verified installation can return `offline-keep`. Admin overrides are
rejected by this demo launcher. It also refuses existing BEVO IPC sockets;
stop other BEVO processes first. After a crash, confirm no daemon remains
before removing stale `/tmp/BEVO_*.sock` files.

## Display and verification

Open these on the server:

- Grafana `http://localhost:3000/d/artemis-demo` — live and historical channels.
- Board health `http://localhost:3000/d/artemis_board_health_rt`.
- Viewer `http://localhost:3001/trackside-live` — sign in and select Artemis.

Use the Artemis datasource. Live rows replace a moving window; history rows
honor the selected time range. Board-health tiles show last age, while its plot
preserves bucket maxima. Grafana automatically scales seconds to milliseconds
and adjusts the Y-axis; the 500 ms threshold can be above a healthy graph's
visible range. Boards absent from generated traffic can remain unseen.

While BEVO sends, capture incoming observations through the API on the server:

```bash
set -a
source .env
set +a
bazel run --config=local //apps/telemetry/analysis:verify_can_demo -- \
  --evidence /tmp/bevo-demo-evidence.json --duration 15
```

This publishes nothing. It requires fresh repeated samples for seven channels,
saves exact session/timestamp/value identities and compares them with a query
of the captured time range. New sessions must resolve before capture. It reports
sample age, matched count, time bounds, read sources and provisional status.
Recent cached results can remain provisional after persistence; the first
comparison alone does not prove a database read. Missing/stale data, coverage
gaps, schema changes and mismatches fail.
`--signals` selects another explicit channel set for a different reviewed schema.

Stop BEVO and close live dashboard/viewer tabs so they do not warm the cache.
For the standard devtool stack, restart only the gateway and require the same
evidence through Postgres:

```bash
docker restart ingest-query_gateway-1
bazel run --config=local //apps/telemetry/analysis:verify_can_demo -- \
  --check /tmp/bevo-demo-evidence.json --require-postgres
```

The recheck requires non-provisional, Postgres-only results. Do not delete cache
or database volumes. Reopen the clients after the check.

## Keep, offline and recovery

Restart the launcher with the same root: require `keep` and the same activation.
Confirm CSVs and client traces resume. Do not start a second copy concurrently.

For an outage, leave CAN generation running, Ctrl+C the schema-authority
terminal, and run `docker stop mosquitto` on the server. CSV rows must continue
on BEVO. The viewer should report stale samples and live windows eventually
empty; empty board-age tiles are not healthy readings. Stop and restart BEVO
during the outage: require bounded `offline-keep`, the same activation and
resumed logging. A fresh installation cannot pass offline startup.

Restore with `./apps/telemetry/stack/server_devtool.sh up` and rerun the same
`schema-serve` command. Fresh telemetry should resume through MQTT reconnection.
Upstream queues are bounded; outage measurements are not guaranteed to backfill
automatically. Retain the CSVs and rerun the verifier for fresh evidence.

## Cleanup

Ctrl+C the BEVO launcher and authority. Stop the viewer through its normal
process manager. On a dedicated demo server, use
`./apps/telemetry/stack/server_devtool.sh stop`; this preserves data volumes.
Remove only the demo endpoint with `tailscale serve --https=8443 off`, not a
global Serve reset. Delete `vcan0`/`vcan1` only if created for this demo using
`sudo ip link del vcan0` and `sudo ip link del vcan1`. Keep the installation
root and CSVs for later checks.
