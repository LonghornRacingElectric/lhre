CREATE SCHEMA IF NOT EXISTS pilot;

CREATE TABLE IF NOT EXISTS pilot.sessions (
    session_id uuid PRIMARY KEY,
    car_id text NOT NULL CHECK (car_id = 'dummy-car'),
    schema_id text NOT NULL,
    started_at timestamptz NOT NULL,
    duration_seconds integer NOT NULL CHECK (duration_seconds > 0),
    seed integer NOT NULL,
    UNIQUE (session_id, schema_id)
);

CREATE TABLE IF NOT EXISTS pilot.signals (
    schema_id text NOT NULL,
    signal_id smallint NOT NULL,
    name text NOT NULL,
    unit text NOT NULL,
    value_type text NOT NULL CHECK (value_type IN ('int', 'float')),
    PRIMARY KEY (schema_id, signal_id),
    UNIQUE (schema_id, signal_id, value_type)
);

CREATE TABLE IF NOT EXISTS pilot.session_rates (
    session_id uuid NOT NULL REFERENCES pilot.sessions,
    signal_id smallint NOT NULL,
    hz integer NOT NULL CHECK (hz > 0),
    PRIMARY KEY (session_id, signal_id)
);

CREATE TABLE IF NOT EXISTS pilot.fault_events (
    session_id uuid NOT NULL REFERENCES pilot.sessions,
    ts timestamptz NOT NULL,
    fault_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('raised', 'cleared')),
    PRIMARY KEY (session_id, ts, fault_id, kind)
);
