# Replay stream

`GET` takes `eventId`, `startAtTimeMs` and an optional `car`. Without `playing=1`
it returns one SSE snapshot; playing mode advances a millisecond cursor at
`tickMs` intervals. Artemis observations come from the query gateway. The
response keeps the existing car, map and driver widget shapes, including native
`apps1_travel` throttle and preferred `gps_precise` coordinates.

Playback stops polling at the recording end or when the reader disconnects.
Artemis responses identify the recording session; historical Orion responses
retain their packet identity and existing database reader.
