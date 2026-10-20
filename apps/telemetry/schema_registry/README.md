# Server schema authority

Read-only deployment interface for the separate
[BEVO updater](../../BEVO/updater/README.md). It exposes the deployed server's
serialized protobuf descriptor, canonical field manifest, and approved
digest-pinned BEVO releases. It never handles telemetry or changes
MQTT, ingest decoding, bridge RPCs, database models or migrations.

## Configure and run

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
bazel run --config=local //apps/telemetry/schema_registry:registry_binary -- \
  --config /absolute/releases.json
```

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
