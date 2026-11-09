CREATE TABLE IF NOT EXISTS pilot.measurements (
    session_id uuid NOT NULL,
    schema_id text NOT NULL,
    signal_id smallint NOT NULL,
    sample_id bigint NOT NULL CHECK (sample_id >= 0),
    ts timestamptz NOT NULL,
    value_type text NOT NULL,
    value_int bigint,
    value_float double precision,
    quality text NOT NULL CHECK (quality IN ('valid', 'invalid')),
    PRIMARY KEY (session_id, schema_id, signal_id, sample_id, ts),
    FOREIGN KEY (session_id, schema_id) REFERENCES pilot.sessions (session_id, schema_id),
    FOREIGN KEY (schema_id, signal_id, value_type)
        REFERENCES pilot.signals (schema_id, signal_id, value_type),
    CHECK (
        (value_type = 'int' AND value_int IS NOT NULL AND value_float IS NULL)
        OR (value_type = 'float' AND value_float IS NOT NULL AND value_int IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS measurements_signal_time
    ON pilot.measurements (signal_id, ts DESC);
