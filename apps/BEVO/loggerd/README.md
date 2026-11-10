# loggerd

Log Artemis fresh observations independently of uplink filtering. Connect to
cand's logger socket and write one CSV per generated frequency pool:
`artemis_<recordingStartMs>_<pool>.csv`. All files opened by one logger process
share a recording start; only pools receiving observations get files.
`LOGGERD_LOG_DIR` overrides the output directory.

Headers come from the compiled storage policy: `time`, `sample_time_ns`,
`updated_fields`, then qualified field columns in natural numeric order.
Arrays use indexed columns. Unobserved fields are blank; zeros and false are
values. `updated_fields` is a JSON list of qualified field names, so a freshly
recorded empty array is distinguishable from a missing array. Rows retain the
original receipt timestamp and contain only fresh fields in that pool.

Each file uses a buffered writer, flushed every 100 rows or one second.
SIGINT/SIGTERM flush before exit. Forced kills or power loss can lose buffered
rows; this is not a durable journal. Cand's bounded logger queue reports
backpressure independently of publishing. There is no automatic CSV-to-database
backfill in this change; logsync continues to transfer the files as CSV.

Build with `bazel build //apps/BEVO:pi_core_binaries`; run
`bazel test //apps/BEVO/loggerd:loggerd_test`. There is no runtime schema JSON
input and no Orion logging mode.
