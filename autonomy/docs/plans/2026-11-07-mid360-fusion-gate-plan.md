# Mid-360 and camera fusion gate implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkboxes for tracking.

**Implementation status, 2026-11-08:** Gate selection, complete launch-command
provenance and regression coverage are implemented. The full colcon run
reports 1,056 tests, zero failures and two native-FMU tests skipped because no
FMU artifact was supplied. All 286 Gazebo checks pass, including both cloud
sources and estimator modes. Independent review found no implementation
issues. Official red-mesa validation remains pending access; skidpad and the
FMU/gate integration dependencies below remain outstanding. The Mac
development smoke tripped `LIDAR_STALE` at the unchanged 0.3 s timeout and is
retained as a failure, not acceptance evidence. With the documented 1.5 s Mac
diagnostic timeout, the initial acceleration check failed stay-stopped
(0.69 m/s after its first stop). After the classifier fixes, three
[acceleration repeats](../../testing/2026-11-08-side-classifier-fixes/README.md#acceleration-follow-up)
pass; the earlier single-run failure was not reproduced. The
[development results](../../testing/2026-11-08-mid360-fusion-gate/README.md)
retain both campaigns; official acceptance and sustained gate-machine rates
remain unchecked below.

**Local driving update:** after #230 integration (`874e0a15`), all 15
autocross runs (seeds 1–5, three repeats each) pass, in addition to the three
acceleration repeats. Actual arguments confirm the combined sensor stack and
Mac 1.5 s timeout. Live camera interruption/recovery, skidpad and gate-machine
rate/default-timeout evidence remain pending.

**Goal:** Close [#206](https://github.com/LonghornRacingElectric/lhre/issues/206) by selecting Mid-360 and camera fusion for gate runs, verifying the planner and LiDAR fallback, and recording sensor rates on red-mesa.

**Architecture:** Select the sensor stack in the existing gate runner, which the issue explicitly permits. Keep standalone Gazebo launch defaults and truth baselines intact. Reuse the existing fusion, planner wiring, scorer and regression tests; change perception algorithms only if a reproduced failure requires it.

**Tech stack:** Bash, Python/pytest, ROS 2 Jazzy, Gazebo Harmonic, colcon, MkDocs.

**Spec:** [Issue #206](https://github.com/LonghornRacingElectric/lhre/issues/206) and [Sim v2 pass criteria](sim-v2-pass-criteria.md).

## Global constraints

- The gate's perception settings are `perception:=lidar lidar_source:=mid360 enable_stereo_camera:=true enable_camera_lidar_fusion:=true`, with `estimator:=ekf safety:=true`.
- The official machine is red-mesa, native Ubuntu 24.04 with an RTX 3060, headless, with the default `safety_lidar_timeout_s` of 0.3 s. Mac Docker runs are development evidence only.
- Use the same sensor and planner settings across every course. No per-seed tuning or timeout relaxation for acceptance.
- No autonomy node may consume truth except for the explicitly allowed initialization and sensor-model uses in the criteria. Truth remains available for scoring.
- Raw recordings remain outside git; results, provenance and selected figures belong under `autonomy/testing/`.
- The full Sim v2 gate also requires `plant:=fmu`, the expanded 35-run matrix and 59-run fault matrix. This issue owns its sensor configuration and validation, not the FMU integration or the whole gate rewrite.

## Current state and dependencies

- PR #226 added the criteria only. Gazebo still declares `lidar_source=gazebo`, `enable_stereo_camera=false` and `enable_camera_lidar_fusion=false`.
- `run_gate.sh` selects LiDAR/EKF/safety but supplies none of #206's three sensor arguments. Its truth baselines use `perception:=sim estimator:=truth`, which cannot accept Mid-360 or fusion.
- Fusion already routes the planner to `/lhr/perception/cones_classified`, enables LiDAR side inference, and uses color-aware boundary pairing. Existing tests exercise camera expiration/recovery and preservation of orange finish/exit cones.
- Earlier campaigns passed Mid-360 and camera fusion separately. They do not establish that the combined stack passes with the FMU on the gate machine.
- There is no committed skidpad world yet: [#114](https://github.com/LonghornRacingElectric/lhre/issues/114) blocks that acceptance row. The old MVP runner waives it; that waiver cannot establish completion of #206 or Sim v2.
- [#85](https://github.com/LonghornRacingElectric/lhre/issues/85) and [#86](https://github.com/LonghornRacingElectric/lhre/issues/86) remain open fallback risks: cross-track links on narrow courses and first-scan side labels that lock in errors. Reproduce failures before deciding whether their fixes must precede acceptance.
- FMU integration/pacing (#202/#203, PRs #221/#222) and the revised course-derived scorer/matrix remain dependencies for full Sim v2 acceptance. Gate timing [#227](https://github.com/LonghornRacingElectric/lhre/issues/227) landed through #230 on 2026-11-08: run caps and five-second settling now use simulation time, with a wall-time hang guard. The runner still uses the MVP matrix and truth-baseline lap reference.

## Review focus

1. Gate arguments must not leak into truth baselines and make them fail launch validation.
2. The combined Mid-360/stereo/fusion graph must use EKF for driving and clean plant pose for the caster.
3. Missing or stale camera evidence must restore LiDAR side namespaces, then recover camera priority when fresh evidence returns.
4. Orange or unknown cones must remain available for acceleration and skidpad geometry.
5. Missing skidpad, absent rate samples, or an old resumed run must not be presented as new passing evidence.

## Task 1: Select and test the gate sensor stack

**Modify:** `autonomy/testing/tools/gate/run_gate.sh`, its adjacent `README.md`, `autonomy/ros2/src/lhr_gazebo/test/test_lidar_source.py`, `test_camera_lidar_fusion_launch.py`, and the Gazebo package `README.md`. Shell-only runner checks live in the new `test_gate_runner.py`, so they can also run without ROS; the existing rules-course scoring checks remain intact.

**Interface:** `plan()` continues emitting `run_id launch_argument...` rows. Each non-baseline row includes the three new sensor arguments; baseline rows retain their existing compatible stack. `args.txt` records the complete launch command, including common arguments, generated world and `EXTRA`, while remaining the marker written last for completed runs.

- [x] Add runner-plan checks for Mid-360, stereo and fusion on every gate row for both autocross styles, acceleration and a fixture skidpad world. Assert that baseline rows retain sim/truth and do not enable the incompatible sensor stack. Run the tests and confirm they fail before implementation.
- [x] Add the shared sensor arguments to the gate rows only. Preserve run IDs, expected-run accounting, resume behavior and baseline matching. Document that the runner now selects #206's sensors and use a fresh `OUT` for all validation; old runs in an existing output directory are skipped by design.
- [x] Record the full executed arguments in `args.txt`, preserving its write-last completion contract. Exercise `one_run()` with stubbed ROS/process commands to compare the invoked arguments with the saved arguments without launching Gazebo or running real cleanup commands.
- [x] Extend launch evaluation for `lidar_source:=mid360` plus stereo/fusion in truth and EKF modes. Assert that `lidar_sim` exists, the GPU cloud is remapped away from `/lhr/lidar/points`, the caster uses clean plant odometry, the detector has `classify_sides=True`, and the planner consumes `/lhr/perception/cones_classified` with `boundary` pairing. Preserve the existing typed-override/startup checks.
- [x] Run the focused tests in the ROS environment, then the full `lhr_gazebo` tests. Update the adjacent READMEs with gate-specific selection, baseline behavior and the fresh-output requirement. Commit this as the gate configuration change.

From `autonomy/ros2`, with the ROS environment and built overlay sourced:

```bash
python3 -m pytest src/lhr_gazebo/test/test_rules_autocross.py src/lhr_gazebo/test/test_camera_lidar_fusion_launch.py src/lhr_gazebo/test/test_lidar_source.py -q
bash -n ../testing/tools/gate/run_gate.sh
colcon test --packages-select lhr_gazebo
colcon test-result --verbose
```

Expected: the focused assertions pass, shell syntax passes, and colcon reports no failures. Skipped ROS checks are not passing launch evidence.

## Task 2: Verify planner fallback and reproduce its known risks

**Inspect/test:** `autonomy/ros2/src/lhr_perception/test/test_camera_lidar_fusion.py`, `test_cone_side_classifier.py`, and `autonomy/ros2/src/lhr_gazebo/test/test_camera_lidar_fusion_launch.py`. Modify their owning implementations and adjacent READMEs only for reproduced defects.

**Interface:** The planner's classified topic preserves cone IDs and geometry. Fresh confirmed blue/yellow evidence supplies sides; expired/unusable evidence uses the latest LiDAR side or unknown namespace. Orange preserves source geometry rather than disappearing from the planner input.

- [x] Run the existing fusion-to-planner camera-loss/recovery and orange-finish tests, plus combined-source launch checks. No additional expiry or ambiguity tests were needed.
- [x] Reproduce #85 using a 3.0 m straight with 2 m cone spacing, and #86 using a deliberately wrong initial side proposal followed by independent corrective proposals. The first labels 20 of 40 cones incorrectly with votes +1/-0.9. The second retains seeded `[LEFT, RIGHT]` after ten `[RIGHT, LEFT]` proposals. These pure reproductions establish the algorithm limitations, not closed-loop behavior under motion; their fixes remain in the owning issues.
- [ ] On red-mesa, perform a separate controlled camera interruption and restoration. Record planner input before loss, after the configured camera-age limit, and after recovery, alongside the resulting centerline. Keep this deliberate interruption separate from the clean campaign. Verify orange/unknown cones survive the transitions.
- [ ] If a failure is new to this integration, add a failing regression, apply the smallest shared fix, update the adjacent README, and rerun the owning tests before the campaign.

From `autonomy/ros2`:

```bash
python3 -m pytest src/lhr_perception/test/test_camera_lidar_fusion.py src/lhr_perception/test/test_cone_side_classifier.py src/lhr_gazebo/test/test_camera_lidar_fusion_launch.py -q
```

Expected: integration checks pass; pre-existing #85/#86 failures remain identified until fixed. No closure claim rests solely on synthetic tests.

## Task 3: Run the course matrix and record sensor rates

**Create when testing actually happens:** `autonomy/testing/<actual-date>-mid360-fusion-gate/README.md`, `results.csv` and `log.md`. Update `autonomy/testing/README.md` and root `mkdocs.yml` to index the campaign. Raw bags and rate logs remain with the campaign data outside git.

**Interface:** Each result identifies its run, commit, machine, actual launch arguments, plant, course/seed and measured outcomes. Rate evidence names its topic, observation interval and time basis. Missing samples are reported as missing, never zero or a nominal configured rate.

- [ ] Build the stack, run one headless Mid-360/fusion/EKF/safety smoke run on red-mesa, and inspect actual publisher/subscriber endpoints. Confirm the planner's classified input and that no driving node has gained a truth subscription. Run one full stack at a time; the recorded FMU experiments show CPU saturation with parallel runs.
- [ ] While the full stack runs, capture `ros2 topic hz` output for `/lhr/lidar/points` and both `/lhr/sensor/zed2i/{left,right}/image_rect` topics. Check QoS compatibility and collect a driving interval, not just startup. Record observed mean rate and maximum interval, the observation duration, and real-time factor from `/clock` against elapsed wall time. Label wall-time rates explicitly; they cannot be compared directly with a ROS-time safety timeout. Use these existing tools first; add no monitoring service.
- [ ] Run autocross seeds 1–5 three times each, acceleration three times, and skidpad three times once #114 lands: 21 clean runs. Keep settings identical and record rates on the combined stack. Use distinct outputs for development and acceptance; rerun failures after their fixes and retain their history.
- [ ] Exercise `rules_autocross` seeds 1–5 with the same sensor stack as part of the shared Sim v2 campaign, then the extreme and real-layout sets when available. Reuse that campaign's evidence instead of adding a second full gate under this issue.
- [ ] Validate with the FMU once its dependencies land. A Gazebo-plant campaign is useful development evidence but cannot establish full Sim v2 acceptance. Use the revised gate timing/scorer for the official campaign; retain its course-derived lap reference and pose-lag check.
- [ ] Report completion, stop distance, cone hits, off-course count, maximum CTE, maximum EKF error, lap timing and health against the criteria. The limits include zero cone hits/off-course/safety trips, CTE at most 1.0 m, EKF error at most 0.5 m and event stop distances of 30/75/25 m. Rates have no independent numeric pass threshold in #206; report measurements and retain the 0.3 s safety timeout.
- [ ] Publish the compact results and provenance, link raw data in SharePoint after upload is verified, and document any blocked acceptance rows. Do not call the old runner's skidpad waiver a pass. Commit the evidence separately from the configuration change.

## Task 4: Verify and prepare the change for review

- [x] In Ubuntu/ROS or the Mac development container, run `./scripts/build.sh`, `colcon test`, then `colcon test-result --verbose` from `autonomy/ros2`. The new tests are discovered and pass. Only the two artifact-dependent native-FMU tests skip.
- [x] Run `uv run --group docs mkdocs build --strict` from the repo root. Check the gate README, Gazebo README, plan and campaign links. Bazel does not build or test autonomy.
- [ ] Review the diff and campaign results against all three #206 requirements. Title the eventual PR `Autonomous: Make Mid-360 and camera fusion the gate stack`; describe remaining dependencies explicitly. Close #206 only after its course/fallback and red-mesa rate evidence exist, including skidpad.

## Completion checklist

- [x] Default gate runs select Mid-360, stereo and fusion; truth baselines still launch.
- [ ] Combined-stack planner wiring and live fallback/recovery are verified.
- [ ] Seeds 1–5, acceleration and skidpad have recorded, passing evidence.
- [ ] Camera and LiDAR rates are recorded on red-mesa with machine, load and time basis.
- [x] Documentation and automated tests pass (two native-FMU tests require an artifact); remaining acceptance work and development-run failures are explicitly recorded.
