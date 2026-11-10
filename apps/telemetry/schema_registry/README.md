# Server schema authority

Read-only deployment interface for the separate
[BEVO updater](../../BEVO/updater/README.md). It exposes the deployed server's
serialized protobuf descriptor, canonical field manifest, and approved
digest-pinned BEVO releases. It never handles telemetry or changes
MQTT, ingest decoding, bridge RPCs, database models or migrations.

## Configure and run

For the current deployment from a cloned repository, use the running
containers as the authority. A branch name or `git pull` alone does not
identify what the server is executing: the last loaded images may still be
older. Rebuild both schema consumers from the intended checkout:

```bash
./apps/telemetry/stack/server_devtool.sh build kafka ingest
```

The images package their matching generated contract and descriptor at
`/schema/`. This is metadata from the same Bazel inputs as their protobuf
bindings; neither daemon reads new runtime schema definitions.

Create an operator-owned `releases.json`, preferred-first:

```json
{
  "releases": [
    "ghcr.io/longhornracingelectric/bevo-core@sha256:replace-with-the-64-character-image-digest"
  ]
}
```

An empty list is valid: a compatible installed release still suffices; an
incompatible/missing installation has no install candidate. Mutable tags,
missing configuration and malformed digests are rejected. Configuration is
loaded at startup; restart this service after changing it.

```bash
./apps/telemetry/stack/server_devtool.sh schema-serve \
  --config /absolute/releases.json
```

This foreground command starts the authority with `--deployed`. It inspects
the actual running `ingest` and `kafka-bridge` container IDs/image IDs, copies
and validates both packaged schemas on each HTTP request, and requires them
to agree. A container replacement during the read, stopped/restarting
consumer, mounted-over metadata, missing metadata, or different schemas
returns HTTP 503. It never substitutes the checkout's or its own bundled
schema. The profile includes those deployment identities for inspection.
The devtool handles Docker's sudo requirement; a direct Bazel invocation can
run `//apps/telemetry/schema_registry:deployed_registry_binary` with
`--docker-sudo`. This target has no dependency on the checkout's textproto
artifacts, so starting it does not regenerate the schema. Container names are
configurable with
`--ingest-container` and `--bridge-container`.

Older deployed images need one rebuild of both consumers to acquire `/schema`.
Changing the checkout without rebuilding them leaves the advertised schema
unchanged. Rebuilding both updates the authority on its next request without
restarting it. Keep the foreground command running under your existing
service manager for persistent deployment; it does not launch with `build`.

The default bind is `127.0.0.1:8085`. `/profile.json` exposes the semantic
schema, descriptor digest and approved OCI references;
`/schema_descriptor.pb` exposes the serialized descriptor. Put this service
behind the server's authenticated HTTPS reverse proxy for Pi access. The
updater verifies the descriptor hash and requires HTTPS except on loopback.

Container deployment is independent of the data stack:

```bash
bazel run //apps/telemetry/schema_registry:schema_registry_load
export TELEMETRY_SCHEMA_RELEASES=/absolute/releases.json
docker compose -f apps/telemetry/schema_registry/docker-compose.yml up -d --no-build
```

Compose also publishes only on loopback. Bazel builds the executable/runfiles
and includes its schema artifacts. **Deploy this authority from the same
revision/artifacts as the deployed ingest image**, not the newest checkout.
Rebuilding this service alone does not update the actual server schema.
This container's default mode is an explicit build-time snapshot, not the
host's `--deployed` mode. Prefer `schema-serve` for the cloned-server workflow;
the authority container is not granted Docker access.

## Update sequence and live verification

The isolated Docker check can run entirely on a development machine:

```bash
bazel test --config=local //apps/BEVO/updater:docker_updater_test --test_output=errors
```

It starts the actual server consumers and dependencies using Bazel-built
images and temporary data, then tests HTTP reconciliation, stopped-consumer
failure preservation and restart recovery. No GHCR credentials or Pi are
needed. See the [updater test notes](../../BEVO/updater/README.md#local-regression-tests)
for its isolation and coverage boundaries.

BEVO initiates the exchange when an operator or scheduler runs its `check`
or `update` command. It fetches `/profile.json` and the matching descriptor,
verifies their identities, and computes the intersection against its own
installed release. If another executable is needed, it pulls an approved
prebuilt GHCR digest. Before activating downloaded files or a new plan it
fetches the profile again; changed deployment/schema/release approval aborts
activation and preserves `current`. There is no GitHub deployment requirement
or server-initiated push to BEVO.

An existing publisher loads its plan once. Coordinate a BEVO update/restart
with server schema deployments; the final profile recheck is not a distributed
transaction and cannot prevent a server change after that check. This does
not verify database migrations or change proto3 default-value semantics.

For a real server check without installing a reverse proxy, open an SSH
tunnel from the test machine:

```bash
ssh -N -L 18085:127.0.0.1:8085 user@SERVER
```

In a second terminal, with an available GHCR release approved in `releases.json`:

```bash
export BEVO_SERVER_URL=http://127.0.0.1:18085/profile.json
bazel test --config=local //apps/BEVO/updater:live_server_test \
  --test_env=BEVO_SERVER_URL --test_env=BEVO_SERVER_TOKEN \
  --test_env=BEVO_REGISTRY_USER --test_env=BEVO_REGISTRY_TOKEN \
  --test_output=errors
```

Set registry credentials only when the package is private. This manual target
downloads the real approved artifacts, installs into a temporary directory,
verifies the deployed schema identity in the activated plan and checks that
a repeated update keeps that release. It does not touch `/opt/bevo`, execute
ARM binaries, or restart services. It can run on Linux/macOS without a Pi;
executing the daemons and verifying CAN hardware still require a Pi.

Normal `bazel test //...` instead exercises a real loopback HTTP authority and
the standalone updater with locally built OCI artifacts, including failure
preservation when the authority returns 503. Deployment-reader tests cover
mixed/replaced containers and the actual packaged metadata paths. They need
neither Docker nor GHCR; the live target is opt-in and needs both the running
server and an available approved release.

## Server admin override

Existing component Compose variables such as `TELEMETRY_INGEST_IMAGE` let an
admin pin manually loaded images or registry digests. Use the actual variable
in each component's Compose file. `TELEMETRY_SCHEMA_REGISTRY_IMAGE` separately
overrides this service's image. No automatic server executable updater is added.

For a manually deployed server build, publish its matching
`schema_contract.json` and `schema_descriptor.pb` together. Run the authority
with `--schema-directory /absolute/deployed-schema`, or set
`TELEMETRY_SERVER_SCHEMA_DIR`, to explicitly replace its built-in schema.
For containers mount that directory read-only and set the variable to its
container path in a local Compose override. Startup verifies that manifest
identity and descriptor definitions agree; there is no latest-schema fallback.

The server's declared schema remains the source of truth. Admin overrides
change deployment selection, not compatibility. Database migrations and
server deployment stay operator-managed; existing ingest behavior is preserved.
