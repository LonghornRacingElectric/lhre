# SQL utilities

SQLAlchemy connection settings, shared drive-day/track models and query helpers.
Supported cars are Orion and Artemis; unqualified sensor model aliases refer to
Orion. Angelique and Nightwatch are retired.

Both car model sets come from the
[Bazel telemetry generator](../../../../lib/codegen/telemetry/README.md).
Orion keeps its packet/FK tables. Artemis uses frequency pools with
`(session_start_ms, time, sample_time_ns)` raw keys and double-precision floating measurements.
Do not create Artemis tables with ORM `create_all`: Timescale chunking and
read functions require the [explicit schema command](../../scripts/README.md).

The existing [Kafka writer](../../stack/processors/db_writer/README.md) owns
protobuf persistence for both cars. Artemis cross-group reads aggregate into
common time buckets before joining by session and time. IDs, enums and booleans
retain their native types.

Artemis raw rows are partial fresh observations. Bucket read models remain keyed
by session/millisecond bucket; do not use those views to infer raw sampling.
Recording-session revisions and `ArtemisIngestProgress` mirror the generated
query metadata. The existing writer advances them inside its telemetry
transaction; imports without Kafka offsets also invalidate live baselines.
