SELECT create_hypertable(
    'pilot.measurements', by_range('ts', INTERVAL '1 hour'),
    if_not_exists => TRUE, create_default_indexes => FALSE
);

ALTER TABLE pilot.measurements SET (
    timescaledb.enable_columnstore = true,
    timescaledb.segmentby = 'signal_id',
    timescaledb.orderby = 'ts DESC'
);

CREATE MATERIALIZED VIEW IF NOT EXISTS pilot.measurements_1s
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 second', ts) AS bucket,
       session_id, schema_id, signal_id,
       sum(COALESCE(value_float, value_int::double precision)) AS sum_value,
       count(*) AS sample_count
FROM pilot.measurements
WHERE quality = 'valid'
GROUP BY 1, 2, 3, 4
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'pilot.measurements_1s', start_offset => INTERVAL '1 day',
    end_offset => INTERVAL '1 second', schedule_interval => INTERVAL '30 seconds',
    if_not_exists => TRUE
);
