# lhr_demo

Launch files for the kinematic stack. `mvs_demo.launch.py` is the one the
gate and the stability check both run, so it is where a run's independent
variables are declared and where its provenance is stamped.

## Why the launch file owns provenance

`git_sha` is resolved here, not in a node, and from
`Path(__file__).resolve().parent` rather than the working directory. A run
started from anywhere but the checkout would otherwise record `unknown`
or, worse, an unrelated repository's commit. `build.sh` passes
`--symlink-install`, so the installed copy resolves back into the
checkout. A `-dirty` suffix marks uncommitted changes, because a
clean-looking id on a modified tree is worse than no id at all.

Every argument that changes an outcome is also passed to `lhr_metrics` for
the CSV row. **A new argument that affects a result belongs in both
places**, or rows quietly stop explaining themselves.

## Arguments

Beyond the node parameters listed in
[ros2/README.md](../../README.md#parameters):

| Argument | Default | What it does |
| --- | --- | --- |
| `use_sim_time` | `true` | Every node but `sim_kinematic` follows `/clock`. Off means wall time, and averaged metrics wander |
| `timeout_sec` | `120.0` | Wall-clock watchdog, so a dead sim ends the run instead of hanging |
| `output_csv` | `data/metrics.csv` | Where the metrics row goes |
| `run_id` | timestamp | Shared by the metrics row and the bag directory |
| `record` | `false` | Record an MCAP bag of the contract topics |
| `bag_dir` | `data/bags` | Parent directory for bags |
| `foxglove` | `false` | Serve the live graph over websocket |
| `foxglove_port` | `8765` | Bridge port |
| `scenario` | `mvs_demo` | Name recorded on the row |

`use_sim_time` reaches `sim_kinematic` as `publish_clock` instead, because
a clock source that waits on its own clock never ticks.

## Recording

```bash
ros2 launch lhr_demo mvs_demo.launch.py record:=true
ros2 bag info data/bags/<run_id>
```

MCAP is the default storage in Jazzy, so recording needs nothing extra
installed. The bag lands in `data/bags/<run_id>` using the same `run_id`
the metrics row carries, which is the whole point: a row reporting a bad
number names the recording that explains it.

`RECORDED_TOPICS` in the launch file is an explicit list, not `--all`.
The viz topics republish every cone marker at rate and would dominate the
bag, and the list doubles as a statement of what the seam between lanes
is. `/clock` is in it because a sim-time bag without it has no timeline a
viewer can read. `/lhr/lidar/points` stays in even though the kinematic
stack never publishes it, so the same recorder works unchanged under
Gazebo and on the car; a topic nothing publishes costs nothing.

Expect roughly 6 MiB per 17 s lap, dominated by the cone `MarkerArray`
topics. That is why `record` is off by default: the gate runs many seeds.

The recorder is spelled twice, once per clock mode, because
`--use-sim-time` is a bare flag that no substitution can switch, and
passing it when nothing publishes `/clock` leaves the recorder waiting on
a clock that never ticks.

Each bag also carries `run_id`, `git_sha`, `scenario` and `seed` as
rosbag2 `custom_data`, readable in `metadata.yaml`, so a bag found on
disk months later still says what produced it.

### A trap worth knowing

`run_id` defaults to `%Y%m%dT%H%M%S`, with a `T` and not an underscore,
because launch passes parameters through a YAML file and YAML reads
`20261004_120000` as the integer `20261004120000`: underscores are digit
separators. That silently renamed the run and left the bag directory and
the metrics row disagreeing, which defeats the correlation above. The
metrics node now also accepts a numerically-typed `run_id` and coerces
it, so a hand-passed id cannot crash a run over a cosmetic field.

## Viewing

```bash
ros2 launch lhr_demo mvs_demo.launch.py foxglove:=true   # live
```

Then open Foxglove and connect to `ws://localhost:8765`. Needs
`ros-jazzy-foxglove-bridge`, which `package.xml` declares, so a normal
`rosdep install` provides it.

For a recorded run, open the bag directly in Foxglove instead; no bridge
and no ROS install involved, which is also what makes MCAP the right
format for the sensor work that happens outside ROS.

Either way, load the shared layout from
[`foxglove/lhr_sim.json`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/foxglove/lhr_sim.json)
so everyone is looking at the same panels. A layout kept only in a
personal Foxglove install means two people debugging the same bag see
different things.

**The layout is not yet verified against the Foxglove app.** It is
hand-written JSON in the documented export shape. If Foxglove rejects it
or a panel comes up empty, fix it in the app and re-export over the file
rather than working around it.

## Ending a run

The metrics process exiting emits a launch `Shutdown`, so the whole graph
comes down on its own and a headless run cannot hang. The exit code does
not survive `ros2 launch`, though, since `LaunchService` returns non-zero
only when launch itself raises. Gates go through
[`scripts/run_headless.sh`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/scripts/run_headless.sh).
