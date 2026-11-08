# GitHub Actions

Workflow behavior, BEVO's manual GHCR publication button/command, schema
change gating and required package permissions are documented in the
[CI runbook](../../docs/ci.md).

Executable builds and release packaging use Bazel. PR checks do not publish;
the unified CI workflow's `publish` input explicitly enables its gated BEVO release job.
