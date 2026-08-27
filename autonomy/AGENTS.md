# Agent instructions: autonomy/

This tree is the exception to the root [AGENTS.md](../AGENTS.md). It is a
ROS 2 workspace, not a Bazel package tree
([ADR-009](../docs/architecture/009-autonomy-outside-bazel.md)).
`autonomy` is in `.bazelignore`; `bazel test //...` builds and tests nothing
here. Do not add `BUILD.bazel` files.

## Build, test, verify

- Supported environment: Ubuntu 24.04 native, ROS 2 Jazzy, Gazebo Harmonic
  ([GETTING-STARTED](ros2/GETTING-STARTED.md)). macOS uses the Docker image
  ([ros2/docker](ros2/docker/README.md)). No WSL.
- Build: `cd ros2 && ./scripts/build.sh` (colcon). Test: `colcon test`
  from `ros2/` (ament lint only today; functional tests are open work for
  the Sim & Test Infra lane).
- CI: `.github/workflows/autonomy.yml` at the repo root runs the same
  build and `colcon test` on every PR that touches `autonomy/`. Not a
  required check. PRs that touch only `autonomy/` skip the Bazel jobs in
  presubmit. The lint rules it enforces are listed in
  [ros2/README.md](ros2/README.md#tests-and-ci); run them locally before
  pushing if you can.
- The demo scripts (`run_demo.sh`, `run_gazebo_demo.sh`, `rviz_demo.sh`)
  need a display and a ROS install. Do not run them in CI or from a
  headless agent.
- Without a ROS install (any Windows machine, most agents) you can edit and
  reason about the code but cannot build or run it. Say so instead of
  claiming a change was verified.

## Rules that bite

- Everything is LF. `autonomy/.gitattributes` enforces it for tracked
  files; on Windows run `sed -i 's/\r$//' <file>` on anything new before
  committing.
- `ros2/build`, `ros2/install`, `ros2/log`, and `ros2/data/metrics.csv` are
  gitignored build/run outputs. Never commit them.
- Packages map to software lanes (table in [README.md](README.md)). Keep a
  change inside the owning lane's packages where possible.
- Docs next to code, same as the root rule: change behavior, change the
  adjacent README in the same change. [`ros2/README.md`](ros2/README.md) is
  the reference (packages, topics, parameters, state machine).
  [`docs/plans/`](docs/plans/README.md) are working docs for contributors
  and agents, not onboarding material. These pages are on the published
  site with the rest of the repo: a new page needs a `nav` entry in the
  root `mkdocs.yml`, and `uv run --group docs mkdocs build --strict` from
  the repo root must pass (it fails on broken links).
- The live plan, timeline, and hardware decisions are in Notion (VMS /
  Autonomous). If a doc here disagrees with Notion, Notion wins.
