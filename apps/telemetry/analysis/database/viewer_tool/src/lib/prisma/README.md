# Car metadata and replay

Car and drive-day lookups resolve the database and replay bounds. Artemis sample
reads go through the query gateway. Replay finds the next raw observation and
assembles fields with that exact nanosecond timestamp and recording identity;
nearby samples remain separate and missing subsystem fields remain absent.
It preserves peaks, booleans and arrays without averaging a future time window.

Gateway consistency and continuation rules are described in the
[query client guide](../query/README.md).
