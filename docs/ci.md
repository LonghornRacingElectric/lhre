# CI workflows

The unified `ci.yml` checks pull requests, pushes to `main` and
`vms/telemetry-pdr`, weekly and manual runs. `All checks passed` aggregates the
path-gated jobs. PR and push checks never publish artifacts.

## Deployment releases

Publication requires an explicit **Run workflow → publish** selection. After
verification, that manual run publishes the Bazel-built ARM64 BEVO core and a
matching server contract bundle. Generated artifacts come from the tested revision, including
executable-only fixes; publication is not conditional on a schema-hash change.
Wire compatibility is checked against the PR base or previous push revision.

The bundle binds the source revision, protobuf descriptor/semantic manifest,
CAN definitions, storage contract, generated additive SQL, wire lock and BEVO
image digest. Both artifacts get `sha-COMMIT` tags. The deployment channel moves
only after publication, only for the configured branch, and only when that
commit is still its head. Publication jobs serialize channel promotion. The
server resolves discovery tags to immutable digests before using them.

An operator publishes a reviewed revision using **CI → Run workflow →
publish**, or:

```bash
gh workflow run ci.yml --ref YOUR_BRANCH -f publish=true
```

`TELEMETRY_DEPLOY_BRANCH` is a repository variable, defaulting to
`vms/telemetry-pdr`. Only manual publication on that branch can promote the
deployment channel. Manual publication elsewhere creates immutable releases
without admitting them to automatic server discovery.
GHCR uploads require `packages: write`; all verification jobs retain read-only
permissions. Registry credentials use `GITHUB_TOKEN` and are removed after the
job. Existing organization packages must grant this repository Actions access.

Publication does not deploy server software or migrate a database. The
[server catalog](../apps/telemetry/schema_registry/README.md) caches verified
metadata. The selected release is prepared during operator deployment; only
matching running consumers and applied storage become authoritative. BEVO then
selects its release at coordinated startup after modem/network initialization.

The telemetry job builds and smoke-tests images. Its Compose integration tests
need the configured server secrets; without them, those integration tests skip
with a warning. The explicit Docker updater test uses disposable local services
and needs no deployment credentials or GHCR publication.
