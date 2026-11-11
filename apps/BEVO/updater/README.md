# BEVO release updater

Separate deployment tooling, not part of `cand`, `loggerd` or `publishd`.
It checks the [server schema authority](../../telemetry/schema_registry/README.md),
keeps a compatible installed executable release, or installs an approved
prebuilt OCI release. It computes a release-specific numeric intersection
for `publishd`; MQTT topics, protobuf message definitions, packet-ID
handshakes and server ingest stay unchanged. It never builds on the Pi or
loads new protobuf types into an executable.

## Build and publish

On the build machine:

```bash
bazel build //apps/BEVO:pi_core_image //apps/BEVO/updater:standalone
bazel run //apps/BEVO:pi_core_push -- --tag release-name
```

Authenticate the build machine to GHCR first. CI publication is manual; PR and
push checks only validate. Use the pushed **image digest**, not a tag, in the server's
release list. The default repository is
`ghcr.io/longhornracingelectric/bevo-core`. Private packages need a Pi token
with `read:packages`; see [GHCR authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

An explicit manual CI `publish` run publishes the core image and a matching
server contract bundle after verification. Only a manual run on the configured
deployment branch promotes the discovery channel; other reviewed revisions can
be published without admitting them to that channel. See the
[CI runbook](../../../docs/ci.md).

The image is an artifact carrier, not a container run on the Pi. Layer one
contains `pi_core_manifest.json`, `schema_contract.json`,
`schema_descriptor.pb` and `can.json`. Layer two contains exactly
`bin/cand`, `bin/loggerd` and `bin/publishd`. OCI layers and individual files
are digest-verified. Dashboard and logsync are not included.
The CAN JSON is hashed for release auditing and preferred-build selection; decode tables and CSV
columns are compiled into the executables, not loaded from this file.

Copy `bazel-bin/apps/BEVO/updater/bevo_updater.pyz` to the Pi. It needs only
Python 3.9+: no Docker, Bazel, Cargo, pip installation, source checkout or
compilation. Field metadata is validated against serialized protobuf
descriptors at build/server startup; the updater checks descriptor digests.

## Check and update

```bash
python3 bevo_updater.pyz check --root /opt/bevo/core \
  --server-url https://telemetry.example/schema/profile.json
python3 bevo_updater.pyz update --root /opt/bevo/core \
  --server-url https://telemetry.example/schema/profile.json
```

Remote downloads require HTTPS; HTTP is permitted only on loopback. Private
registry credentials use `BEVO_REGISTRY_USER` / `BEVO_REGISTRY_TOKEN` and are
not stored. Set `BEVO_SERVER_TOKEN` when the server's reverse proxy requires
Bearer authentication. `--repository` explicitly changes the allowed repository;
candidates from other repositories and mutable tags are rejected.

For a server deployed from a clone, run the authority through the
[server's `schema-serve` command](../../telemetry/schema_registry/README.md).
That mode checks the matching metadata in the running ingest and bridge
containers on every request. GitHub does not deploy or synchronize the
server; checking out a branch without rebuilding those images has no effect.

After fetching the descriptor and selecting/downloading a release, the CLI
fetches the profile again immediately before activation (or returning a
`check`/`keep` result). Changed deployment, schema or approved releases abort
the operation with the existing activation intact. This catches changes
during a download; it does not lock the server against later deployments.

The server profile is authoritative and orders approved releases
preferred-first. Automatic catalog profiles request a build matching the deployed
protobuf and CAN definitions. Identical executable metadata is kept; a changed
plan alone is reconciled without a binary download. Manual profiles retain the
compatible-installed-release policy. `--upgrade` selects the first compatible approved release even when
the installed one suffices. Shared numeric paths must preserve names, types,
cardinality, units, enum meanings and array layouts. Additive protobuf fields
may be absent on either side; equal schema hashes are not required.
Incompatible shared definitions are excluded from the uplink while other
shared fields continue. This is an intersection of the actual installed or
selected release and the deployed server, independent of other available
images. Renames at the same numeric path are conservatively excluded;
numbers alone cannot establish unchanged meaning. Enum values must exist
with the same meaning on both sides. Repeated fields retain their full
ordered layout; elements are never renumbered or shifted.

Artemis releases require `projection_version: 2` and
`wire_format: artemis_updates_v1`, covering values and presence together.
Legacy schema fixtures retain version 1. Active BEVO has no Orion fallback.
Older binaries require a prebuilt supported replacement from the approved
GHCR list. The updater never compiles a replacement. No shared measurement,
incompatible root fields, or no usable prebuilt release is an error.

`check` only reports the decision. `update` stages and verifies all three
executables plus their matching schema/audit assets and `reconciliation.json`, then atomically switches
`/opt/bevo/core/current`. Only current and previous managed releases are kept;
there is no persistent image/build cache. Failed validation/download leaves
the active symlink unchanged. No compatible candidate is an error, not an
arbitrary fallback.

When the installed executable release suffices but the server schema has
changed, action `reconcile` stages those same verified executables with a new
plan, without contacting GHCR. `keep` means both release and plan already
match. Activation identity includes the executable metadata and plan digest;
the previous complete release/plan remains available for rollback. Plans are
validated against both manifests and bound to the exact executable digests.

The updater does not restart running services; output reports `restart_required`.
Set `BEVO_CORE_ROOT=/opt/bevo/core` in the telemetry service environment and
restart at an operator-approved time. The [launcher](../nonhermetic/README.md)
resolves `current` once, so all core processes use the same release. The
separately installed dashboard still uses `BEVO_BIN_DIR`.

`publishd` loads the selected plan once at startup. Run the updater and
coordinate a telemetry restart whenever the authoritative server schema
changes; an already-running publisher does not poll it. CAN acquisition,
IPC consumers and local logging retain the complete producer snapshot.
Only pit-bound publishing projects fields. Missing/invalid plans fail
startup; malformed snapshots are dropped without publishing original bytes.

Artemis projection filters values and explicit presence masks together. Omitted
fields do not become fresh zero observations in native batches. CAN layout
identity is separate from protobuf compatibility; passive decoding cannot prove
that flashed board firmware matches those definitions.

## Startup synchronization

The bootstrap service powers GPS and the cellular modem through the existing
GPIO scripts and configures CAN first. It then runs `startup`, allowing up to
60 seconds for the complete synchronization attempt before resolving the core release and
starting daemons. Configure `/etc/default/bevo-telemetry`:

```bash
BEVO_CORE_ROOT=/opt/bevo/core
BEVO_UPDATER=/absolute/bevo_updater.pyz
BEVO_SERVER_URL=https://telemetry.example/schema/profile.json
BEVO_NETWORK_WAIT_S=60
```

The Pi bundle includes the standalone updater. Keep it and GPIO bootstrap
scripts outside managed executable releases. On network, registry or validation
failure, `startup` verifies the installed executables and their existing plan
before returning `offline-keep`. Missing/damaged activations fail updater verification. If the updater itself
cannot execute, bootstrap pins the pre-existing activation and continues the
normal launcher checks; the publisher still validates its release-bound plan. A
running stack checks every five minutes but never activates or restarts itself;
apply a new release at the next coordinated startup. Explicit admin-pinned
`BEVO_CORE_RELEASE` bypasses automatic startup synchronization.

## Admin override

A manual release directory contains the same four metadata/assets files and
`bin/` executables:

```bash
python3 bevo_updater.pyz override --root /opt/bevo/core --release /mnt/manual-release
python3 bevo_updater.pyz clear-override --root /opt/bevo/core
```

An override persists: automatic checks/updates report it without contacting
server or registry until cleared. Local digests/platform are verified, but
server compatibility selection is explicitly bypassed; the admin owns that
decision. For a projection-capable release the override creates an explicit
identity plan against its own schema, preserving all its defined fields.
This deliberately bypasses the server comparison. Clearing does not change
installed/running executables. The next
update checks compatibility. A retained previous release can be the manual
rollback source.

`BEVO_CORE_RELEASE=/absolute/installed-release` directly pins
the launcher, taking precedence over `BEVO_CORE_ROOT`; that directory still
requires a validated release plan. Explicit overrides are
not automatic fallbacks and do not make incompatible schemas compatible.

## Local regression tests

With Docker running, exercise the deployed-container path without GHCR or a Pi:

```bash
bazel test --config=local //apps/BEVO/updater:docker_updater_test --test_output=errors
```

This opt-in test loads the Bazel-built images and starts actual ingest and
bridge processes with isolated Kafka, MQTT and PostgreSQL. The database starts with an older additive storage fixture built by the normal
generator, then preparation applies the selected release artifacts. It checks that the authority
reads the running images, the standalone updater activates a matching plan
with unchanged core executables, and a repeated update keeps that release.
Stopping the bridge must produce HTTP 503 and preserve the activation;
restarting it must restore successful checks. The test removes its own
containers/network and uses temporary database/install state. It does not
reuse the normal stack's volumes, publish to GHCR or exercise sensor delivery.
Its initial core files come from the locally built release layers; the
separate live-server test covers actual registry downloads.
Both live-state targets always execute when selected; Bazel cannot reuse a
previous passing test result instead of checking the current Docker/server state.

The package test runs an actual loopback HTTP authority and the standalone
updater archive, verifies plan activation from the server's schema and
preserves the old installation on HTTP 503. It never needs a Pi or registry.

For the real deployed server and GHCR path, use the opt-in
`//apps/BEVO/updater:live_server_test` target described in the
[authority runbook](../../telemetry/schema_registry/README.md#update-sequence-and-live-verification).
It installs into a temporary root and verifies a second update keeps the
release. It requires an approved available GHCR digest and actual server URL;
private services also require credentials. A Pi is needed only for executing
the ARM daemons and checking hardware, not for this copy/activation test.

```bash
bazel test --config=local //apps/BEVO/updater:updater_test //apps/BEVO/updater:package_test
```

The updater tests inject registry responses and download, staging-write and
activation failures. They require the active release to remain intact and a
failed activation to be retryable. The package test reads the Bazel-built OCI
layout directly from runfiles; it never pulls/pushes an image or contacts GHCR.
It also reconciles that current car release against a changed server field,
requiring only the affected numeric path to be excluded while the installed
producer schema stays unchanged.
All installation roots are temporary. These tests run in normal presubmit
and postsubmit without credentials, Docker or a Pi. Real GHCR authentication
and running the ARM executables remain a manual deployment check.

The Docker updater test also prepares additive Timescale storage, preserves an
existing row, sends zero/false through MQTT → Kafka → the actual writer, and
runs the pool-storage correctness cases against its disposable database.
