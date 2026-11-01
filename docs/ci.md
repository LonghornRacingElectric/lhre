# CI workflows

The unified `ci.yml` builds and tests on PRs, pushes to `main`, weekly and
manual runs. Its BEVO job adds explicit ARM executable and release-package
checks; telemetry runs Docker-backed integration checks when its service
secrets are configured. `All checks passed` aggregates every job, including
the optional release job. Ordinary pushes/PR checks do not publish BEVO images.

## Publish a BEVO schema release

In Actions, select **CI → Run workflow**, choose the branch, enable
`publish`, and enter `schema_base`, preferably the last released commit SHA.
`HEAD^` compares with the selected revision's immediate parent; it does not
mean the last published release. To invoke the same operation from a shell:

```bash
gh workflow run ci.yml --ref YOUR_BRANCH -f publish=true -f schema_base=LAST_RELEASE_COMMIT
```

The workflow file must exist on the default branch for manual dispatch;
write access is required. See [GitHub's dispatch documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

The selected revision and baseline each build their own
`//lib/codegen/telemetry:schema_contract` and `:can_json` using Bazel.
Comparing parsed generated definitions avoids publishing for comments or
formatting alone, and includes CAN mapping changes even when protobuf tags
stay unchanged. Using each commit's own generator also detects generator
changes that affect definitions. A baseline without those targets or a failed
baseline build is an error, never permission to publish.

Unchanged definitions skip GHCR publication. Changed definitions require all
verification jobs in that CI run to pass, including core release tests,
textproto propagation/intersection, the full Bazel suite and formatter. Then a separate manual-only
job authenticates with `GITHUB_TOKEN` and invokes
`bazel run //apps/BEVO:pi_core_push -- --tag sha-COMMIT`. Docker is used only
for registry login; executable compilation and OCI packaging use Bazel.
Only the publication job has `packages: write`. PR jobs have read-only
repository permissions and cannot enter that job.

The output is `ghcr.io/longhornracingelectric/bevo-core`. Its source annotation
links it to this repository. If an existing package does not allow this
repository's Actions token to write, grant the repository access in the
package's Actions permissions. No additional PAT is needed when that access
is configured. See [publishing with GITHUB_TOKEN](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

The run summary prints the immutable image digest. Publishing does not change
the deployed server schema, approve the release, update the Pi, or restart
services. An operator adds that digest to the
[server authority](../apps/telemetry/schema_registry/README.md)'s release
list; the [BEVO updater](../apps/BEVO/updater/README.md) then selects an
available compatible release. Existing sufficient executables are kept.

For bootstrap or executable-only changes with unchanged schema/CAN mapping,
the explicit local Bazel push command in the updater runbook remains
available. No mutable `latest` tag or automatic deploy is introduced.
