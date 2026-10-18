# Synthetic Aurora Circuit demo

These three logger-format JSON exports replace the earlier test set. Aurora
Circuit is a freshly randomized, irregular closed loop about 957 m long,
with alternating broad sweeps and tighter inward bends. Generation seed:
`846a6d0d`. The shape was checked for self-intersections (none). Its coordinates
are near 38.2° N, 115.6° W, unrelated to Pickle Lot or the previous test circuit.
GPS and telemetry are synthetic, not real driving data.

Use **Import** in the viewer and select all three files in any order.
`layout.json` is recognized and registered before the driver sessions:

- `layout.json`: one slow counter-clockwise layout recording. The simulated
  antenna is 1 ft left of the intended centre; edge generation shifts it right
  and assumes 11.5 ft total width.
- `run-driver-a.json`: Test Driver A, three completed circuits in each direction,
  generally earlier braking and one hesitant corner exit.
- `run-driver-b.json`: Test Driver B on the same routes, with later/harder braking,
  different lines and a missed apex followed by a correction on the second circuit.

Driver paths vary between circuits instead of repeating an exact centreline.
Speeds follow corner limits, braking-distance planning and bounded acceleration;
throttle and braking ramp over time. The cars cross lap marks at driving speed,
with a recording pause between directions. Telemetry arrives at 10 Hz, GPS at
roughly 2 Hz with mild drift and jitter. Driver B has a short GPS dropout while
telemetry continues. Temperature, power and energy channels remain synthetic;
this is an illustrative driving simulation, not a validated vehicle model.

Reimport all three files to load the replacement geometry and matching drivers.
This set uses new session IDs containing the generation seed, so the old browser
imports cannot silently replace or mask it. Select **Synthetic Aurora Circuit**
in the run picker; delete old test entries separately if you no longer need them.

They appear under **Oct 18, 2026 · Synthetic Aurora Circuit** in the
America/Chicago timezone. Select the driver and direction in the run picker,
then use **Ghost → Driver** or **Compare another file** for the same direction.
Importing the layout alone opens its saved road immediately. It remains in the
picker’s **Layouts** tab as one track after driver files are added, separate from **Runs**.
The normal workspace stays visible with blank analysis panels until a driver is selected.
Use the layout row’s Delete button to remove the saved layout without deleting drivers. Unique Aurora session IDs
keep these imports separate from real runs and previously imported demo data.
Replacing these files does not delete old demo imports from browser storage.

## Track edges

The three files support importing a new track, automatic GPS direction labels,
same-direction route matching and driver comparison directly. They deliberately
do not match the bundled Pickle Lot layout.

The viewer now recognizes `layout.json` as a centreline recording and creates
both directions' edges in the browser. For other filenames, use **Import layout**.
If the driver files are already imported, importing only `layout.json` upgrades
their matching saved runs; reimporting the drivers is unnecessary. No Aurora
layout is hardcoded into the app.

The standalone preparation script can also generate an asset for bundling:

```bash
node scripts/build-layout.mjs test-data/layout.json /tmp/aurora-layout.json "Synthetic Aurora Circuit"
```
