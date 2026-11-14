# BobSim correlation plan

Status: draft for the Nov 15 review.

Correlate BobSim's high-fidelity tier, the BobLib Modelica model, to Orionwatch one subsystem at a time, scored with the wheel speeds, the CSM corner boards and the IMU. Fix the inputs and the measured parameters first, tune only the few parameters each test isolates, and keep laps for validation only. Every stage has a numeric gate no tighter than the car's own run-to-run scatter, and every test those three sensor groups cannot score is marked with what would unlock it.

## What changes from the original plan

The original idea holds: a fixed spec list, simple to complex, steady state before transient, run on the car then in the sim, fix the gap, repeat. Seven changes make it work in practice.

1. **Correlate the Modelica model through a replay of the car's own inputs.** BobLib's `VehicleFMI` takes handwheel angle and pedal positions, the same entry point lhre already builds for the autonomy plant. Logged inputs drive it, and its outputs are scored against the car.
2. **Add a Stage 0 with no driving.** `vehicle/bundle.yml` marks the hardpoints, springs, toe, camber, bar rates and brake bias as copied from BobSim's own car (status `assumed`), and the masses and inertias have no named source. Stage 0 measures Orionwatch as it runs now, so real errors don't hide inside fitted numbers.
3. **Add a sensor check before any comparison.** On the UTA drive days the CSM and USM boards never reported. The plan assumes the wheel speeds, the CSMs and the IMU are live, and each channel gets a known-input check before its data counts.
4. **Correlate subsystems on measured states before full replay.** Each stage first feeds one sub-model measured states (for example, measured lateral acceleration into the roll model), and only then replays the whole vehicle. One error can't then compound across every trace.
5. **Vertical, then longitudinal, then lateral.** Ride and pitch behaviour sets the normal loads that every tire force depends on, and speed and drag set the operating point of every lateral test, so they are frozen first.
6. **Replay the driver's measured inputs instead of asking for perfect maneuvers.** Driver variation becomes an input, not an error, and repeats only need to measure the car's scatter.
7. **Mark what three sensor groups can't score.** Body sideslip, path and absolute speed under wheel slip need GPS or an optical speed sensor. Those gates are listed as blocked, with what unlocks them, rather than quietly dropped.

Full laps stay a hold-out: they validate the frozen model and are never used to tune it. The gate for every metric is the larger of an engineering tolerance and twice the measured run-to-run spread of that metric on the car.

## What the Modelica model holds

The target is BobLib's `VehicleSim` and its input-driven twin `VehicleFMI` (BobLib `75b6e03`, pinned by BobSim `f58f932`), compiled with our records by `make records`. It is a MultiBody model, so it has no fixed DOF list. With the variants lhre uses, I count about 15 mechanical DOF plus driveline and tire-lag states; the compiler's state list will confirm the exact number.

| Part | How the model does it | DOF or states | Seen on the car by |
| --- | --- | --- | --- |
| Body | Rigid sprung mass in two halves joined by a torsion spring and damper (`FrameCompX`) | 6, plus 1 frame twist | IMU; CSM ride heights |
| Suspension | Double wishbone with bellcrank and anti-roll bar front and rear, as real linkages; spring and damper are lookup tables, not single rates | 4 (one travel per corner, the unsprung vertical motion) | CSM sus pots and corner accels |
| Steering | Rack and pinion driven by handwheel angle through a filter | Driven input | Steering column angle |
| Wheels | Spin on rigid hubs; six-axis wheel compliance exists but is off | 4 spin | USM wheel speeds |
| Tire | MF5.2 combined slip with transient slip (relaxation length); vertical stiffness from the `.tir` | Lag states per tire | CSM strain-gauge loads; IMU transients |
| Driveline | Motor, fixed ratio, clutch LSD, halfshaft stiffness and damping, inboard rear brakes | Rotor, LSD and shaft states | Inverter speed and torque; rear left versus right wheel speed |
| Aero | CFD map of loads against ride height | None | Coastdown by wheel speed; CSM ride heights |
| Atmosphere and road | Constant density, flat road | None | Logged by hand |
| Driver and VCU | Takes handwheel angle and accelerator and brake pedal; BobLib's own VCU turns pedal into torque | None | Pedals, torque command and feedback |

BobSim already scores this model with `standard-eval-steady-state`, `standard-eval-transient`, `standard-eval-ramp-steer` and `standard-eval-four-post`, and BobLib has K&C rigs (`KnCSteerable`, `KnCLocked`). The plan runs the same metric code on car logs, so both sides are scored identically.

**What it fixes from the first draft.** The clutch LSD, tire relaxation, nonlinear spring and damper tables, frame torsion (it uses the 300 kN·m/rad figure) and true linkage kinematics are all in this model. The DynPy gaps the first draft listed are closed.

**Gaps that remain.** If a stage keeps failing after its parameters are fitted within physical limits, the cause is likely one of these, and the fix is a model change, not a bigger fudge factor:

- **Pedals, not torque.** `VehicleFMI` takes pedal positions, so a replay also scores BobLib's VCU against Orionwatch's. The fix is a variant that takes measured motor torque and brake pressure (BobLib work), or the SIL VCU in the loop. Until then, Stage 4 compares torque before anything else.
- **Starting state.** lhre sets `modelica_initialization: legacy`, so the model settles from a pose solved for no particular car. Replays need to start from measured speed and ride heights, which needs a solved pose for our record (BobLib work, per `simulation/docs/bobsim.md`).
- **Rigid links and hubs.** No bushing or upright compliance, and wheel compliance is off and uncalibrated. Toe compliance was already raised in #dynamics.
- **Aero is constant.** The ride-height reference points sit above both CFD grids, so aero clamps at the grid corner, as in DynPy.
- **Fixed air density, no wind.**
- **Tire file with no source.** The `.tir` has no named origin; grip is scaled per session (see the staged plan).

One parameter is worth a quick check before any fitting: wheel inertia is 0.02 kg·m² per corner, which looks low for a 10 inch wheel and tire.

## What the car can measure

With wheel speeds, the CSMs and the IMU live, every model DOF can be scored except body sideslip, and frame twist only as a bound. The critical bus (steering, pedals, torque, brake pressure) did log at UTA, so replay inputs are there. Rates are from `lib/spec`.

| Quantity | Live channel (rate) | Status |
| --- | --- | --- |
| Wheel spin (4) | USM wheel speed (333 Hz) | Direct. Rear left versus right shows how much the LSD locks. |
| Longitudinal speed | Front wheel speeds; IMU ax | Good while the fronts roll free. Under hard braking, integrate IMU ax across the slip window. |
| Yaw rate | IMU gyro; CSM corner gyros as a cross-check (100 Hz) | Direct, after mounting-angle and lever-arm correction. |
| Roll, pitch, heave | CSM ride heights (100 Hz); IMU rates | Direct, after a volts-to-mm calibration. |
| Suspension travel (4) | CSM sus pots (100 Hz) | Direct, after calibration against jacked heights. |
| Normal load (4) | CSM strain gauges on the pushrods | Direct, after a known-load calibration. |
| Tire deflection | Ride height minus suspension travel | Estimate only (inferred); good enough to check tire vertical rate. |
| Lateral and longitudinal acceleration | IMU | At the CG after lever-arm correction. |
| Frame twist | Front roll minus rear roll from the four ride heights | Bound only. |
| Unsprung vertical acceleration | USM accel (333 Hz) | Only if the USM sends it as well as wheel speed. |
| **Body sideslip** | None | **Blocked.** See below. |
| **Position and path** | None | **Blocked.** See below. |

**Inputs for replay.** Steering column angle (333 Hz), pedal positions, inverter torque command and feedback (333 Hz), the VCU torque path (100 Hz), and front and rear brake pressure (333 Hz) are all on the critical bus.

**Not on any bus, so logged by hand each session:** tire pressures hot and cold, tire surface temperatures, tire set and heat cycles, track and air temperature, setup sheet, ballast, driver.

### What these sensors can't score, and what would

| Blocked | Stages hit | What unlocks it |
| --- | --- | --- |
| Body sideslip (gradient and trace) | 5, 7 | RTK GPS velocity with a solid lock, or an optical ground-speed sensor |
| Path and segment times | 8 | GPS, or timing beacons at segment lines |
| Absolute speed while all four wheels slip | 4 | GPS speed; until then, IMU integration over short windows |
| Road-wheel angle and steering compliance | 0, 5, 6 | A rack position sensor, or toe measured under load in Stage 0 |
| Frame twist as a number | 5, 7 | Strain gauges on the frame; the ride-height bound is enough to rule it in or out |
| Wind | 3 | An anemometer, or coastdowns on still-air days in both directions |

## The staged test plan

Nine stages, each freezing what later stages depend on. Gates are proposed starting values for the Base review to challenge; each widens to twice the car's own run-to-run spread if that is larger. A gate marked blocked can't be scored with wheel speed, CSM and IMU alone.

| Stage | Car test | Live sensors used | Isolates, then tunes and freezes | Gate (proposed) |
| --- | --- | --- | --- | --- |
| 0. Measure Orionwatch | Corner scales with driver; CG height by axle-lift tilt; unsprung parts weighed; hardpoints from CAD or measurement; spring rig and damper dyno; motion ratios by pushing each corner; steering ratio and toe under load on turn plates; tire loaded radius and vertical rate under known loads; wheel inertia by spin-down | CSM ride heights and sus pots at rest | Replaces every `assumed` value in `bundle.yml` with `measured`. Nothing is fitted. | Static loads within 1% per corner; static ride heights within 1 mm; camber and toe gain within 10% of BobLib's K&C rig |
| 1. Check the sensors | Static tilt for IMU orientation; jack each corner to known heights; known loads on the strain gauges; known steering angles; a measured roll-out for wheel-speed radius; one sharp tap for timing across USM, CSM and IMU | All | Calibration tables and time offsets for every channel. Nothing in the model changes. | Each channel within its rated accuracy; cross-board timing under 5 ms |
| 2. Vertical | Push-and-release heave and pitch at rest; slow drive over a plank or curb | CSM ride heights, sus pots, corner accels; USM accel if sent | Damper tables, tire vertical rate and damping; compared on BobLib's four-post model first | Heave and pitch frequency within 5%; damping ratio within 0.05; wheel-hop frequency within 10% (needs USM accel) |
| 3. Longitudinal, steady | Coastdowns both directions with zero torque; constant-speed runs at 3 to 4 speeds | Front wheel speeds; IMU ax | Drag area, rolling resistance, driveline loss | Drag area within 5%; speed trace RMS under 0.3 m/s |
| 4. Longitudinal, transient | Straight-line launches at stepped torque limits; stops at stepped brake pressures | Wheel speeds (slip ratio); IMU ax; CSM pitch and loads | First the pedal-to-torque path, then longitudinal tire stiffness and peak grip, brake gain, pitch gradient (checks anti geometry) | Motor torque trace NRMSE under 5%; peak ax within 5%; pitch gradient within 10%; ax trace NRMSE under 10%; speed under full slip is blocked |
| 5. Lateral, steady | Constant radius with slowly rising speed, both directions; slow ramp steer at constant speed | IMU ay and yaw; CSM roll and loads; wheel speeds | Linear range first: front and rear cornering stiffness, roll stiffness split. Then the limit: front and rear peak lateral grip. | Understeer gradient within 0.5 deg/g; roll gradient within 10%; lateral load transfer within 10% (strain gauges); max ay within 3%; sideslip gradient blocked |
| 6. Lateral, transient | Step steer and sine steer at 15 and 20 m/s and 5 deg (the speeds BobSim's TransientEval uses); a swept or random steer for frequency response | IMU yaw and ay; steering angle | Yaw inertia if not measured; tire relaxation length, which this model now has | Yaw and ay steady gain within 5%; response time within 20 ms; overshoot within 5 points; frequency-response gain within 1 dB and phase within 10 deg up to 2 Hz |
| 7. Combined | Brake in turn, lift-off in turn, power-on exit | IMU; rear left and right wheel speeds; CSM loads | Combined-slip tire terms; LSD preload and locking | Yaw rate and ay trace NRMSE under 15%; rear wheel-speed difference within 10%; sideslip trace blocked |
| 8. Laps (hold-out) | Autocross and endurance laps | IMU; wheel speeds | Nothing. Validation only. | Overlapping GG envelopes; minimum corner speeds within 3%; lap time within 2% (timing beam); segment times blocked without GPS or beacons |

**Each stage runs in two passes.** Pass A drives one sub-model with measured states: for example, measured ay into the roll model, or measured slip and strain-gauge load into the tire. Pass B replays the logged driver inputs through `VehicleFMI`. Pass A localizes an error to one subsystem before Pass B can blur it across all of them.

**Why this order, where it differs from steady-state-then-transient.** Vertical dynamics come early even though they are transient: they set the normal loads every tire force scales with, they barely depend on the tire's lateral behaviour, and they can be tested at walking speed. Longitudinal comes before lateral because speed, drag and slip set the operating point of every cornering test. Within each axis the original rule holds: steady state first, then transient.

**Tire grip is a session parameter, not a car parameter.** Start from the 0.60 to 0.66 scale on TTC peak grip suggested in #orionwatch-operations, but scale peak grip (LMUX, LMUY) separately from cornering stiffness (LKX, LKY), because one blanket factor also changes steering response. Every test day opens with a short reference run on the same skidpad, which sets that day's grip scale.

## The worry points from the Slack thread

All four worry points from the #vehicle-modeling-and-software correlation thread have an owner stage.

| Worry | How the plan handles it | Where |
| --- | --- | --- |
| Turning operating conditions into an equivalent run; tire scaling for degradation and road surface | Log tire set, heat cycles, pressures and temperatures by hand each session. Treat grip scaling as a per-session value set by a daily reference skidpad run, so wear and surface show up as a drift in that value instead of polluting car parameters. Scale peak grip and cornering stiffness separately. Hold pressure and temperature windows fixed for tuning runs. | Stage 5 reference run, every test day |
| Correlating every DOF from one run hides where an error starts | Never fit on a full run. Each stage owns a few parameters, runs Pass A on measured states first, and freezes before the next stage. Full replays use short windows that restart from measured states, so drift cannot compound past a few seconds. | Every stage, Pass A then B |
| Telling model error from execution error | Three layers. Sim execution: BobLib's own regression baselines, and the same maneuver run on DynPy as a sanity check, catch solver and setup faults. Measurement: Stage 1 calibrations and timing checks. Driver execution: replay uses logged inputs, so a missed maneuver is still a valid comparison. An error counts as model error only if it is larger than the repeat spread and survives all three. | Stage 1, plus a sim self-check before every comparison |
| Driver deviation (law of large numbers feels heavy) | Replaying measured steering, torque and brake inputs removes the need for identical runs. Repeats are only needed to measure the car's own spread: 3 runs per condition and direction is a reasonable start. Steady-state windows are cut automatically with the same settle tolerances BobSim's steady-state eval uses. The autonomy stack could later drive repeatable steer inputs. | Stages 5 to 8 |

## Tooling pipeline

The pipeline is a BobSim study inside `simulation/studies/`, so it inherits the existing `make study` runner, `identity.json` provenance and candidate-vehicle checks instead of building a parallel system.

```text
car logs ──> ingest ──> clean and align ──> segment by stage
                                                  │
                                                  v
vehicle.yml + bundle ──> replay VehicleFMI ──> compare (same metric code)
        ^                                         │
        │                                   pass? ── yes ──> freeze, promote, next stage
        │                                         │
        └──── bounded fit of this stage's ─── no ─┘
              parameters only
```

A stage that keeps failing after a bounded fit points at a model-form gap from the list above, which becomes a BobSim change rather than a bigger fudge factor.

1. **Ingest.** Pull full-rate logs from the car's Pi through logsync, not the radio stream. Write one Parquet file per run plus a session sheet: tire set, pressures, temperatures, setup sheet id, ballast, driver, `bundle_id`.
2. **Clean and align.** Apply Stage 1 calibrations, correct IMU mounting angles from a static window, remove cross-board time offsets, resample to one 100 Hz grid with anti-alias filtering, and derive slip ratios, tire deflection and ay at the CG.
3. **Segment.** Cut runs into maneuvers automatically and tag them by stage. Steady-state windows use BobSim's own settle tolerances.
4. **Replay.** Drive `VehicleFMI` with the logged handwheel angle and pedals, using fmpy as lhre's `lhr_sim_fmu` plant and BobSim's FMU runner already do. Start each window from measured speed, yaw rate and ride heights. Three modes: open loop; open-loop steering with speed tracking; and windowed restarts from measured states every few seconds. A torque-input variant replaces the pedals once it exists.
5. **Compare.** Score car and sim with the same code: BobSim's Modelica steady-state, transient, ramp-steer and four-post evals, plus `compare_transient_signals` (RMSE, normalized RMSE, max error, bias). Output one report per stage with overlays and a pass/fail table.
6. **Fit.** Only the parameters the current stage owns, bounded to physical ranges and pulled toward measured values, using SciPy least squares. Set them on the built model at start instead of recompiling, where BobLib allows it. Report how well each parameter is pinned down; if two move together, fix one from measurement instead of fitting both. Keep held-out runs for checking the fit.
7. **Version and promote.** Each fitted set is a candidate vehicle in `out/<name>/` with its own `bundle.yml`. A passing stage becomes a PR that updates `vehicle/vehicle.yml` and cites the correlation report as the source of each changed value. Every promotion reruns all earlier stages against stored logs, the same way `four-post-acceptance` guards FourPost today.

**How it fits the SIL.** Correlation validates the plant; SIL validates the controller. Feeding logged pedal and steering into SIL and comparing its torque path against the car's logged `TORQUE_PATH` and inverter torque separates controller errors from vehicle-model errors. The autonomy FMU plant runs this same Modelica model, so a promoted record carries its correlation level straight into autonomy and SIL results.

**Where LAPDOG fits.** The comparison overlays (car versus sim traces on the same lap) are a natural LAPDOG panel, which also answers the open question in the thread about tying LAPDOG into the tuning loop.

## Open questions

Settled on Nov 14: the model is BobLib's Modelica model, the car is Orionwatch, and wheel speed, the CSMs and the IMU are the sensors to plan around.

- Who owns the Stage 0 measurements of Orionwatch that replace the copied BobSim geometry and setup?
- Which IMU: one on the sprung mass near the CG, or only the CSM corner gyros and accels? The gates assume one near the CG; corner-only works with lever-arm corrections.
- Does the USM send unsprung acceleration as well as wheel speed? The wheel-hop gate in Stage 2 needs it.
- Replay through pedals and BobLib's VCU for now, or add a `VehicleFMI` variant that takes measured torque and brake pressure first?
- Is a GPS lock or an optical speed sensor possible later? It unblocks the sideslip gates in Stages 5 and 7 and segment times in Stage 8.
- Does the car run aero? If so, the ride-height reference points need a named source before Stage 3.
- Is there a flat pad big enough for constant-radius runs and step steer at 20 m/s?
- Do the proposed gates match the measurable goals reviewers asked VMS to show?

## Sources

- [LonghornRacingElectric/lhre](https://github.com/LonghornRacingElectric/lhre) at `ed05b95` (Nov 11): `simulation/README.md`, `simulation/docs/bobsim.md`, `vehicle/vehicle.yml`, `vehicle/bundle.yml`, `docs/can-matrix.md`, `lib/spec/messages/` (CSM and USM), `autonomy/ros2/src/lhr_sim_fmu/README.md`.
- [BobDyn/BobSim](https://github.com/BobDyn/BobSim) at the lhre pin `f58f932`: `simulations/response/mbd/` (Modelica steady-state, transient, ramp-steer and four-post evals and the FMU runner), `docs/boblib-submodule.md`.
- [BobDyn/BobLib](https://github.com/BobDyn/BobLib) at the BobSim pin `75b6e03`: `README.md`, `RELEASE_READINESS.md`, `BobLib.UsersGuide`, `Experiments/Standards/` (VehicleSim, VehicleFMI, K&C rigs), `Chassis/` (frame, suspension, MF5.2 tire and wheel models), `Drivelines/`.
- LHRE Slack: the #vehicle-modeling-and-software correlation thread (Nov 12), #orionwatch-operations ISO-test discussion (Nov 1), #electronics on CSM and USM status (Oct 20 to Nov 10), #dynamics on sensor needs (Jul 11) and toe compliance (Sep 17), and Arnav's answers on model, car and sensors (Nov 14).
