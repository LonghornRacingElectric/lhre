# Agent instructions: autonomy/

This tree is the exception to the root [AGENTS.md](../AGENTS.md). It is a
ROS 2 workspace, not a Bazel package tree
([ADR-009](../docs/architecture/009-autonomy-outside-bazel.md)).
`autonomy` is in `.bazelignore`, so `bazel test //...` builds and tests
nothing here. Do not add `BUILD.bazel` files.

## Build, test, verify

- Supported environment: Ubuntu 24.04 native, ROS 2 Jazzy, Gazebo Harmonic
  ([GETTING-STARTED](ros2/GETTING-STARTED.md)). macOS uses the Docker image
  ([ros2/docker](ros2/docker/README.md)). No WSL.
- Build: `cd ros2 && ./scripts/build.sh` (colcon). Test: `colcon test`
  from `ros2/` (ament lint plus unit and generated-track regression tests).
- CI: the `Autonomy (colcon)` job in the repo's `.github/workflows/ci.yml`
  runs the same build and `colcon test`. It runs when a PR touches
  `autonomy/`, `vehicle/` or the BobSim pin. If it fails, the required
  `All checks passed` check fails. PRs that touch only `autonomy/` skip the
  Bazel jobs. [ros2/README.md](ros2/README.md#tests-and-ci) lists the lint
  rules that the job enforces. Run them locally before you push, if you can.
- PRs that touch `autonomy/` need an approving review from a member of the
  `autonomous` GitHub team. `.github/CODEOWNERS` requests them, and the
  `main` ruleset requires the code-owner approval.
- Title every autonomy PR `Autonomous: <summary>`. The prefix tells other
  teams which PRs are ours. It also makes CodeRabbit skip the PR.
  `.coderabbit.yaml` at the repo root sets this, and it also ignores
  `autonomy/` files.
- Every demo script needs a ROS install. `rviz_demo.sh` and a default
  `run_gazebo_demo.sh` (Gazebo GUI plus RViz) also need a display. Do not
  run those in CI or from a headless agent. `run_demo.sh` opens no window.
  Headless runs use `scripts/run_headless.sh` (kinematic) or
  `run_gazebo_demo.sh gui:=false rviz:=false`. The second needs Gazebo,
  which CI does not install.
- Without a ROS install (any Windows machine, most agents) you can edit and
  reason about the code but cannot build or run it. Say so instead of
  claiming a change was verified.

## Rules that bite

- Everything is LF. `autonomy/.gitattributes` enforces it for tracked
  files. On Windows, run `sed -i 's/\r$//' <file>` on anything new before
  you commit.
- `ros2/build`, `ros2/install`, `ros2/log` and `ros2/data` are gitignored
  build/run outputs. Never commit them. `ros2/data` holds metrics CSVs
  and recorded MCAP bags, which get large fast.

- Hardware test recordings (MCAP, `.npz`, ROS 2 bags) never go in git. They
  live on SharePoint under _VMS_ > Autonomous > Test Data. Only the
  writeup, results table, chosen figures and scripts go in `testing/`
  ([testing/README.md](testing/README.md)).
- Vehicle numbers (wheelbase, track, steering limit, masses, sensor
  mounts) live only in `ros2/src/lhr_vehicle/config/vehicle.yaml`. Nodes
  read them through `lhr_vehicle.load_vehicle()`. Never hardcode them.
- `lhr_gazebo/scripts/generate_vehicle_model.py` generates two models from
  that file: `lhr_gazebo/models/fsae_vehicle/model.sdf` (no cameras) and
  `model_stereo.sdf` (with cameras). Edit the YAML or the templates in
  `lhr_gazebo/templates/`, and run the generator again. Commit the YAML and
  both generated SDFs. `lhr_gazebo`'s tests fail if either committed model
  is stale.
- Packages map to software lanes (table in [README.md](README.md)). Keep a
  change inside the owning lane's packages where possible.
- Docs go next to code, as in the root rule. If you change behavior, change
  the adjacent README in the same change. [`ros2/README.md`](ros2/README.md)
  is the reference (packages, topics, parameters, state machine).
  [`docs/plans/`](docs/plans/README.md) are working docs for contributors
  and agents, not onboarding material.
- These pages are on the published site with the rest of the repo. A new
  page needs a `nav` entry in the root `mkdocs.yml`. From the repo root,
  `uv run --group docs mkdocs build --strict` must pass. It fails on broken
  links.
- When you add or rename a node parameter that the Gazebo stack uses,
  expose it in `ros2/src/lhr_gazebo/launch/gazebo_demo.launch.py` in the
  same change:
  - Declare the launch argument.
  - Forward its `LaunchConfiguration` to the owning node in every
    applicable perception branch (`sim` and `lidar`).
  - Match the node's default. Use explicit `ParameterValue` types for
    strings, floats, integers and booleans, so CLI values keep the declared
    ROS type.
  - Keep intentional mode-specific defaults. Document any parameter that
    you do not expose, and say why.
  - Update the adjacent README with the usage.
  - Verify the defaults and non-default overrides through launch evaluation
    and node startup, in every applicable branch.

  A declared launch argument alone is not evidence that a parameter reaches
  the node. If ROS is unavailable, report which checks remain unverified.
- The live plan, timeline, and hardware decisions are in Notion (VMS /
  Autonomous). If a doc here disagrees with Notion, Notion wins.
