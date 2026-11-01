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

Authenticate the build machine to GHCR first. Publishing is explicit, never
performed by PR CI. Use the pushed **image digest**, not a tag, in the server's
release list. The default repository is
`ghcr.io/longhornracingelectric/bevo-core`. Private packages need a Pi token
with `read:packages`; see [GHCR authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

For a release with changed schema/CAN definitions, use **Actions → CI →
Run workflow**, select the branch, enable `publish`, and set `schema_base` to
the last released commit. The CLI equivalent is:

```bash
gh workflow run ci.yml --ref YOUR_BRANCH -f publish=true -f schema_base=LAST_RELEASE_COMMIT
```

The workflow builds both revisions' generated contract and CAN JSON, skips
publication if they match, and requires its verification jobs to pass before
pushing a `sha-COMMIT` tag. Ordinary PR runs never publish. The workflow must
be on the default branch before its new dispatch inputs become available.
For bootstrap or executable-only releases with identical definitions, use the
explicit Bazel push command above. See the
[workflow runbook](../../../docs/ci.md) for permissions and
the difference between publishing and server approval.

The image is an artifact carrier, not a container run on the Pi. Layer one
contains `pi_core_manifest.json`, `schema_contract.json`,
`schema_descriptor.pb` and `can.json`. Layer two contains exactly
`bin/cand`, `bin/loggerd` and `bin/publishd`. OCI layers and individual files
are digest-verified. Dashboard and logsync are not included.
The CAN JSON is retained for release auditing only; decode tables and CSV
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

The server profile is authoritative and orders approved releases
preferred-first. A compatible installed release is kept without downloading
images. `--upgrade` selects the first compatible approved release even when
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

Only releases advertising `projection_version: 1` support this behavior.
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

The updater does not restart services; output reports `restart_required`.
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

This is wire-field exclusion, not database availability tracking. With
implicit proto3 scalar presence, omitted values decode as defaults; existing
server JSON conversion may therefore show an excluded scalar as zero/false.
It cannot distinguish that from a valid zero without a separate presence
design. See [protobuf field presence](https://protobuf.dev/programming-guides/field_presence/).
The updater does not change the server schema, add a per-frame identity or
make uncoordinated server/BEVO deployment safe.

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
