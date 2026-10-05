# Hardware tests

Results from bench and field tests of autonomy hardware: what was tested,
what passed, and the figures that show it. One folder per test campaign.
The raw data stays out of git.

```text
testing/
  <YYYY-MM-DD>-<name>/
    README.md      # question, result, method, provenance
    results.csv    # one row per run: settings and measured numbers
    log.md         # timestamped log kept during the test
    figures/       # the figures the README and slides use
    *.py           # the scripts that made those figures from the raw data
  tools/<sensor>/  # the scripts that recorded and analyzed the data
```

| Campaign | Question | Status |
| -------- | -------- | ------ |
| [2026-10-04 Mid-360 acceptance](2026-10-04-mid360-acceptance/README.md) | Is the donated, dropped Livox Mid-360 good enough for the car, and how far does it see a cone? | Car sensor, provisional: endurance and connector checks not run |

## Rules

- **Raw data is never committed.** Recordings run 50 to 100 MB per minute.
  Upload each campaign's whole data folder (recordings, ROS 2 bags, health
  logs, every figure) to SharePoint under
  [LHR Electric > Design > _VMS_ > Autonomous > Test Data](https://utexas.sharepoint.com/sites/ENGR-LonghornRacing/LHR%20Electric/Design/_VMS_/Autonomous/Test%20Data),
  in a folder named like the campaign folder here. While testing, the tools
  write to `~/lhr-test-data/<campaign>/` on the test laptop, and
  `~/lhr-test-data/current` points at the active campaign.
- **Commit the figures you will present, not every figure.** The tools write
  two figures per run; pick the ones that carry the result. The rest stay
  with the raw data.
- **Write the log while you test.** Setup changes, faults, people walking
  through the scene, weather. A number without its conditions is not
  reusable.
- **Name runs by setting, keep repeats.** A repeat at the same setting gets
  a `_run<N>` suffix. A bad run moves to `raw/discarded/` with the reason in
  its name and a line in the log, instead of being deleted.
- **Measure, then trust the sensor.** If a reference measurement (a tape)
  disagrees with the sensor by a constant offset, say so and report both.
- **The README answers the question first,** in short, simple technical
  English, like the [simulation studies](../../simulation/docs/studies.md).
