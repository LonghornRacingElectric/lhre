# BEVO release updater

Separate deployment tooling, not part of `cand`, `loggerd` or `publishd`.
It checks the [server schema authority](../../telemetry/schema_registry/README.md),
keeps a compatible installed executable release, or installs an approved
prebuilt OCI release. MQTT, protobuf payloads, packet-ID handshakes and ingest
are unchanged. It never filters samples or loads new types into a daemon.

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
Incompatible shared definitions require another executable, not conversion.

`check` only reports the decision. `update` stages and verifies all three
executables plus their matching schema/audit assets, then atomically switches
`/opt/bevo/core/current`. Only current and previous managed releases are kept;
there is no persistent image/build cache. Failed validation/download leaves
the active symlink unchanged. No compatible candidate is an error, not an
arbitrary fallback.

The updater does not restart services; output reports `restart_required`.
Set `BEVO_CORE_ROOT=/opt/bevo/core` in the telemetry service environment and
restart at an operator-approved time. The [launcher](../nonhermetic/README.md)
resolves `current` once, so all core processes use the same release. The
separately installed dashboard still uses `BEVO_BIN_DIR`.

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
decision. Clearing does not change installed/running executables. The next
update checks compatibility. A retained previous release can be the manual
rollback source.

For unmanaged deployment, `BEVO_CORE_RELEASE=/absolute/release` directly pins
the launcher, taking precedence over `BEVO_CORE_ROOT`. Explicit overrides are
not automatic fallbacks and do not make incompatible schemas compatible.
