# lhr_metrics

`metrics_node` writes one CSV row for a run, including centerline error,
speed, lap status, and optional estimate-versus-truth error. The demo launch
passes `run_id` and `output_csv`; the same run ID names an optional MCAP bag.

The node writes on lap completion or shutdown. It writes at most one row per
node lifetime, and writes before trying to log during shutdown because the
ROS context may already have stopped. A run stopped early still has a row,
but `lap_completed=false` must not be read as a completed circuit.

Run `colcon test --packages-select lhr_metrics` after changes. The metric
definitions and parameters are in the [ROS 2 reference](../../README.md#lhr_metrics-metrics_node).
