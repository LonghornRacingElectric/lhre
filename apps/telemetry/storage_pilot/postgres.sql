CREATE OR REPLACE VIEW pilot.measurements_1s AS
SELECT date_bin(INTERVAL '1 second', ts, TIMESTAMPTZ '1970-01-01') AS bucket,
       session_id, schema_id, signal_id,
       sum(COALESCE(value_float, value_int::double precision)) AS sum_value,
       count(*) AS sample_count
FROM pilot.measurements
WHERE quality = 'valid'
GROUP BY 1, 2, 3, 4;
