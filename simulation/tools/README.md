# Simulation tools

These tools support the Make targets in [simulation](../README.md).
Run them through those targets in Docker. They use public BobSim imports;
they do not change the runtime or copy its implementation.

`check_vehicle.py` implements the loading smoke check. `bundle.py` checks
vehicle identity. `provenance.py`, `invalidation.py` and `readme.py` manage
study records and their published summaries. Their contracts are in
[studies](../docs/studies.md) and [tests](../docs/tests.md).

`build_fmu.py` implements `make fmu`. It generates canonical vehicle records
in temporary build space and imports the pinned upstream exporter. Source
checkout, artifact identity and native test steps are in [FMU](../docs/fmu.md).
It uses `tools.invalidation.git` and `sha256_file` for source checks and
hashes. The shared Git helper handles container mounts owned by another
user and preserves Git's diagnostic when a checkout cannot be read.
