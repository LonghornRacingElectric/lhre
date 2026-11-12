# Telemetry analysis adapters

Artemis series, GPS, exports and recording discovery read through the
[shared query gateway](../query/README.md). `artemisStorage.ts` preserves the
existing nested bucket-row shape while requesting generated signals, supported
aggregates and bounded pages. Default means apply to fields declared
averageable; states, arrays and booleans retain their last fresh observation.
Explicit minima and maxima apply to numeric scalars.
Bucket requests batch up to ten signals and size their time windows for at most
10,000 signal/bucket pairs. Windows meet at bucket boundaries to preserve peaks
and means across chunks. Pagination still handles session boundaries and the
gateway's response byte limit.

Channel definitions come from the generated storage contract. Existing labels
remain usable; pool placement is not hardcoded. Other car readers retain their
separate legacy connections. Track files, chart configuration and export files
remain local analysis metadata.
