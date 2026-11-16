# Extreme-course review fixes, 2026-11-16

All seven fixed worlds regenerate exactly, pass the rules envelope and keep
physical boundary gaps between 0.5 m and 2 m. The focused geometry, spawn and
planning suite passes 72 checks. This establishes fixture and launch plumbing,
not clean laps on all seven courses or Sim v2 acceptance.

## Method

Integrated main through `1a99e037` and the reviewed competition-layout branch
through `ff35ee35`. Resolved the shared generator, scorer, planning and docs
changes rather than retaining the stale `$sensors` path. Extreme and imported
layouts share the fixed-course planning loop; their independent repeat counts
both default to one. Combined-plan tests retain acceleration and skidpad after
both fixed-course sets and verify the shared stack and safety settings.

Dense analytic poses still describe the shapes, but cone placement samples
the longer boundary arc at stations below 2 m. Circular interpolation preserves
the exact hairpin radius; piece and feature endpoints preserve straight lengths.
The 500 m binary search targets the sampled, scored reference rather than the
dense construction curve. Its cone count decreases from 1,080 to 548.

All fixtures stage at the beginning of their first feature. Spawn checks include
the complete canonical footprint against the saved road and cones. Regeneration
compares whole SDF files, including the spawn. Ten-decimal coordinates and
normalized negative zero remove insignificant twelve-decimal differences while
retaining the existing exact-radius regression tolerance. A nine-decimal trial
missed that tolerance on the combined fixture; no tolerance was relaxed.

The width-test correction is isolated in commit `c1bf194f`: the required 3 m
minimum applies to road clearance between boundary segments, not maximum
cone-pair separation. The replacement pins the narrowest clearance to
3.00–3.01 m and checks every pair against it. Turn-chord reserve is necessary
when physical cones are farther apart.

## Validation

Built all 16 ROS packages in the isolated Ubuntu 24.04/Jazzy development
container with read-only source and separate outputs. The focused checks are:

```bash
python3 -m pytest src/lhr_trackgen/test/test_extreme_autocross.py \
  src/lhr_gazebo/test/test_extreme_courses.py
colcon test --packages-select lhr_gazebo lhr_metrics lhr_trackgen
colcon test-result --verbose
```

The three affected package suites report **771 tests, zero errors, failures
or skips**, including ROS lint. Repository Ruff, strict MkDocs and whitespace
checks also pass.

Coverage includes every regenerated scene, minimum and maximum physical cone
gaps including closure, exact 80 m and 4.5 m limits, the 500 m scored length,
whole-file freshness, all saved spawn footprints, both perception/estimator
branches, independent repeat counts, malformed counts and complete mixed plans.
No new driving campaign is claimed. Full-set GPU/default-timeout driving and
the final FMU stack remain required; the deployed dashboard and worker were
not changed by these fixture fixes.

## Historical smoke evidence

The earlier #218 documentation recorded a bounded headless hairpin run with
LiDAR/EKF reaching DRIVING: 909 truth messages, 111 clouds and 90 planner paths
over a 30-second observer window, with 89.3 m travel and 0.585 m maximum
centerline distance. Safety and metrics were disabled. This predates the new
cone placement and spawn, is not a rerun of these changes, and establishes
neither a complete lap nor scored acceptance.
