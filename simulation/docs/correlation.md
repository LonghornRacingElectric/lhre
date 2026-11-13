# BobSim correlation plan

Status: draft for the Nov 15 review.

Correlate BobSim's 14 DOF model to the car one subsystem at a time: fix the inputs and the measured parameters first, then tune only the few parameters each test isolates, and keep full laps for validation only. Every stage has a numeric pass/fail gate, and no gate is tighter than the car's own run-to-run scatter.

## What changes from the original plan

The original idea holds: a fixed spec list, simple to complex, steady state before transient, run on the car then in the sim, fix the gap, repeat. Six changes make it work in practice.

1. **Add a Stage 0 with no driving.** `vehicle/bundle.yml` marks the hardpoints, springs, toe, camber, bar rates and brake bias as copied from BobSim's own car (status `assumed`), and the masses and inertias have no named source. Tuning a model of another car against our logs would hide real errors inside fitted numbers, so we measure first.
2. **Add a sensor check before any comparison.** On the UTA drive days the CSM and USM boards never reported, so ride heights, shock pots, strain gauges, corner IMUs and wheel speeds were flat. Every channel gets a known-input check before its data counts.
3. **Correlate subsystems on measured states before full replay.** Each stage first feeds the sub-model measured states (for example, measured lateral acceleration into the roll model) and only then runs the whole 14 DOF forward. This stops one error from compounding into all 14 traces.
4. **Vertical, then longitudinal, then lateral.** Ride and pitch behaviour sets the normal loads that every tire force depends on, and speed and drag set the operating point of every lateral test, so they are frozen first.
5. **Replay the driver's measured inputs instead of asking for perfect maneuvers.** Driver variation becomes an input, not an error, and we only need enough repeats to measure the car's scatter.
6. **Keep full laps as a hold-out.** Laps validate the frozen model and are never used to tune it, so a passing lap means something.

The gate for every metric is the larger of an engineering tolerance and twice the measured run-to-run spread of that metric on the car.

## What BobSim's 14 DOF model holds

The 14 DOF model is DynPy's `VehicleModel14DOF` (BobSim pin `f58f932`): 6 rigid-body DOF, 4 unsprung vertical masses and 4 wheel spins. It is one of four nested models (3, 6, 10, 14 DOF) that share one force path, so lower-DOF runs are a free cross-check. The Modelica tier (BobLib) is a separate multibody model and is not the 14 DOF model.

| Part | How the model does it | Inputs we must supply |
| --- | --- | --- |
| Body | 6 DOF Newton-Euler on the sprung mass; mass, CG and inertia summed from `vehicle.yml` parts | Mass, CG, inertia tensor |
| Unsprung | 4 vertical masses; tire is a linear vertical spring and damper on road height | Unsprung masses, tire vertical rate and damping |
| Wheel spin | J·dω/dt = applied torque + rolling moment − Fx·r per wheel | Wheel inertia, loaded radius |
| Suspension | Linear wheel rate from the spring table slope over motion ratio squared; one linear damping rate from the damper table end points; linear anti-roll bar | Spring and damper tables, motion ratios, bar rate |
| Kinematics | KinPy lookup tables from hardpoints: camber, toe, caster, KPI, trail, scrub and anti/jacking links versus travel and rack | Hardpoints, rack position |
| Tire | Pacejka MF5.2 forces and moments from the `.tir` file, steady state | `.tir` coefficients and scaling factors |
| Aero | Ride-height map of downforce, drag and moments | Map and ride-height reference points |
| Driver inputs | Mean front road-wheel angle (or rack travel), 4 wheel torques, 4 road heights | Logged steering, motor torque, brake pressure |
| Integrator | SciPy `solve_ivp`; RK45 default, Radau for 10 and 14 DOF | None |

BobSim already computes the metrics we need on the sim side: understeer, roll and sideslip gradients (steady-state eval), and yaw and lateral-acceleration gain, rise time, overshoot and sine-sweep gain and phase (transient eval). The plan runs the same metric code on car logs so both sides are scored identically.

**Gaps that tuning cannot close.** If a stage keeps failing after its parameters are fitted within physical limits, the cause is likely one of these, and the fix is a BobSim change, not a bigger fudge factor:

- **No differential.** Rear torque is split 50/50 left to right, which acts like an open diff. The car has a clutch LSD (`diff_use_lsd: true`), and BobLib models it but DynPy does not. This matters most on power out of corners and on skidpad.
- **Steady-state tire.** No relaxation length, so the model will lead the car in yaw at higher steering frequencies.
- **Linear damping.** One slope across the whole damper table: no bump/rebound split and no knee.
- **No compliance.** Rigid links, no compliance steer or camber, and the 300 kN·m/rad chassis torsion figure is not used by DynPy. Toe compliance was already raised in #dynamics.
- **Symmetric static loads.** Left and right corner loads are always equal, so cross weight cannot be represented.
- **Aero is constant.** The ride-height reference points sit above both CFD grids, so aero clamps at the grid corner (documented in `simulation/docs/bobsim.md`).
- **Fixed air density** of 1.225 kg/m³.

One parameter is worth a quick check before any fitting: wheel inertia is 0.02 kg·m² per corner, which looks low for a 10 inch wheel and tire.

## What the car can measure

On paper the CAN spec can observe every DOF except body sideslip, which needs a fused GPS estimate; tire deflection is also never measured directly. "On paper" matters: the Data Acq bus boards (CSM, USM) were flat on the UTA days, and the CSMs were reported working again on Nov 10.

| DOF | Channels in `lib/spec` (rate) | Status and gap |
| --- | --- | --- |
| Longitudinal velocity | USM wheel speeds (333 Hz), motor speed (333 Hz), GPS speed | Driven-wheel speed from motor rpm only gives the axle average. Front wheel speeds are the clean reference. |
| Lateral velocity (sideslip) | RTK position from the gateway (`gps_precise`), `gps_imu` | **Gap.** Needs GPS velocity heading minus yaw angle, which needs RTK lock and a fused estimate. An optical speed sensor is the robust fix. |
| Heave | CSM ride height and sus pot per corner (100 Hz) | Sus pots and ride heights need a volts-to-mm calibration. |
| Roll, pitch | Same corner channels, plus corner gyros (100 Hz) | Fine once calibrated. #dynamics prefers one sprung IMU near the CG over four corner IMUs. |
| Yaw | Corner gyro z (100 Hz), `gps_imu` | Fine. Needs mounting-angle and lever-arm correction. |
| X, Y, yaw position | RTK GPS | Only needed for laps and path overlays. |
| Unsprung vertical (4) | USM unsprung accel (333 Hz), sus pot relative travel | Wheel hop near 15 to 20 Hz is resolvable at 333 Hz. Tire deflection itself is not measured. |
| Wheel spin (4) | USM wheel speed (333 Hz) | Was flat at UTA. Rear left and right are separate only through USM, not through the motor. |

**Inputs for replay.** Steering column angle (333 Hz), inverter torque feedback and command (333 Hz), the VCU torque path (100 Hz), front and rear brake pressure (333 Hz) and pedal positions are all on the critical bus, which did log at UTA.

**Forces.** The CSM strain gauge channels, once calibrated against known loads, give pushrod force per corner, which is the closest thing to a measured normal load. That would let the tire be checked directly instead of through the whole car.

**Not on any bus, so logged by hand each session:** tire pressures hot and cold, tire surface temperatures, tire set and heat cycles, track and air temperature, setup sheet, ballast, driver.

## The staged test plan

Nine stages, each freezing what later stages depend on. Gates below are proposed starting values for the Base review to challenge; each is widened to twice the car's own run-to-run spread if that is larger.

| Stage | Car test | Isolates, then tunes and freezes | Gate (proposed) |
| --- | --- | --- | --- |
| 0. Measure the car | Corner scales with driver; CG height by axle-lift tilt; unsprung parts weighed; hardpoints from CAD or measurement; spring rig and damper dyno; motion ratio by pushing each corner; steering ratio on turn plates; tire loaded radius and vertical rate under known loads; wheel inertia by spin-down | Replaces every `assumed` value in `bundle.yml` with `measured`. Nothing is fitted. | Sim static loads within 1% per corner; static ride heights within 1 mm |
| 1. Check the sensors | Static tilt for IMU orientation; jack each corner to known heights; known steering angles; known loads on strain gauges; constant-speed run for wheel speed versus GPS; one sharp tap for cross-board timing | Calibration tables and time offsets for every channel. Nothing in the model changes. | Each channel within its rated accuracy; cross-board timing under 5 ms |
| 2. Vertical | Push-and-release heave and pitch at rest; slow drive over a plank or curb | Damper effective rate, tire vertical rate and damping | Heave and pitch frequency within 5%; damping ratio within 0.05; wheel hop frequency within 10% |
| 3. Longitudinal, steady | Coastdowns both directions with zero torque; constant-speed runs at 3 to 4 speeds | Drag area, rolling resistance, driveline loss | Drag area within 5%; speed trace RMS under 0.3 m/s |
| 4. Longitudinal, transient | Straight-line launches at stepped torque limits; straight-line stops at stepped pressures | Longitudinal tire stiffness and peak grip, brake torque gain, pitch gradient (checks anti geometry) | Peak ax within 5%; pitch gradient within 10%; ax trace NRMSE under 10% |
| 5. Lateral, steady | Constant radius with slowly rising speed, both directions; slow ramp steer at constant speed | Linear range first: front and rear cornering stiffness, roll stiffness split. Then the limit: front and rear peak lateral grip. | Understeer gradient within 0.5 deg/g; roll gradient within 10%; sideslip gradient within 0.2 deg/g; max ay within 3% |
| 6. Lateral, transient | Step steer and sine steer at the speeds and amplitudes BobSim's TransientEval already uses (15 and 20 m/s, 5 deg); a swept or random steer for a frequency response | Yaw inertia if not measured; exposes tire lag (a model-form gap) | Yaw and ay steady gain within 5%; response time within 20 ms; overshoot within 5 points; FRF gain within 1 dB and phase within 10 deg up to 2 Hz |
| 7. Combined | Brake in turn, lift-off in turn, power-on exit | Combined-slip tire terms; exposes the missing LSD | Yaw rate and sideslip trace NRMSE under 15% |
| 8. Laps (hold-out) | Autocross and endurance laps | Nothing. Validation only. | Segment times within 2%; minimum corner speeds within 3%; overlapping GG envelopes |

**Each stage runs in two passes.** Pass A drives one sub-model with measured states: for example, measured ay into the roll model, or measured slip angle and strain-gauge load into the tire. Pass B replays the logged driver inputs through the full 14 DOF model. Pass A localizes an error to one subsystem before Pass B can blur it across all of them.

**Why this order, where it differs from steady-state-then-transient.** Vertical dynamics come early even though they are transient: they decide the normal loads every tire force scales with, they barely depend on the tire's lateral behaviour, and they can be tested at walking speed. Longitudinal comes before lateral because speed, drag and slip set the operating point of every cornering test. Within each axis the original rule holds: steady state first, then transient.

**Tire grip is a session parameter, not a car parameter.** Start from the 0.60 to 0.66 scale on TTC peak grip suggested in #orionwatch-operations, but scale peak grip (LMUX, LMUY) separately from cornering stiffness (LKX, LKY), because one blanket factor also changes steering response. Every test day opens with a short reference run on the same skidpad, which sets that day's grip scale.

## The worry points from the Slack thread

All four worry points from the #vehicle-modeling-and-software correlation thread have an owner stage.

| Worry | How the plan handles it | Where |
| --- | --- | --- |
| Turning operating conditions into an equivalent run; tire scaling for degradation and road surface | Log tire set, heat cycles, pressures and temperatures by hand each session. Treat grip scaling as a per-session value set by a daily reference skidpad run, so wear and surface show up as a drift in that value instead of polluting car parameters. Scale peak grip and cornering stiffness separately. Hold pressure and temperature windows fixed for tuning runs. | Stage 5 reference run, every test day |
| Correlating all 14 DOF from one run hides where an error starts | Never fit on a full run. Each stage owns a few parameters, runs Pass A on measured states first, and freezes before the next stage. Full replays use short windows that restart from measured states, so drift cannot compound past a few seconds. | Every stage, Pass A then B |
| Telling model error from execution error | Three layers. Sim execution: BobSim's own DynPy-versus-BobLib checks and a 10-versus-14 DOF comparison catch solver and setup faults. Measurement: Stage 1 calibrations and timing checks. Driver execution: replay uses logged inputs, so a missed maneuver is still a valid comparison. An error counts as model error only if it is larger than the repeat spread and survives all three. | Stage 1, plus a sim self-check before every comparison |
| Driver deviation (law of large numbers feels heavy) | Replaying measured steering, torque and brake inputs removes the need for identical runs. Repeats are only needed to measure the car's own spread: 3 runs per condition and direction is a reasonable start. Steady-state windows are cut automatically with the same settle tolerances BobSim's steady-state eval uses. The autonomy stack could later drive repeatable steer inputs. | Stages 5 to 8 |

## Tooling pipeline

The pipeline is a BobSim study inside `simulation/studies/`, so it inherits the existing `make study` runner, `identity.json` provenance and candidate-vehicle checks instead of building a parallel system.

```text
car logs ──> ingest ──> clean and align ──> segment by stage
                                                  │
                                                  v
vehicle.yml + bundle ──> replay in BobSim ──> compare (same metric code)
        ^                                         │
        │                                   pass? ── yes ──> freeze, promote, next stage
        │                                         │
        └──── bounded fit of this stage's ─── no ─┘
              parameters only
```

A stage that keeps failing after a bounded fit points at a model-form gap from the list above, which becomes a BobSim change rather than a bigger fudge factor.

1. **Ingest.** Pull full-rate logs from the car's Pi through logsync, not the radio stream. Write one Parquet file per run plus a session sheet: tire set, pressures, temperatures, setup sheet id, ballast, driver, `bundle_id`.
2. **Clean and align.** Apply Stage 1 calibrations, correct IMU mounting angles from a static window, remove cross-board time offsets, resample to one 100 Hz grid with anti-alias filtering, and derive sideslip, slip ratios and ay at the CG.
3. **Segment.** Cut runs into maneuvers automatically and tag them by stage. Steady-state windows use BobSim's own settle tolerances.
4. **Replay.** Build `ModelInputs` from the log: handwheel angle through the measured steering ratio to rack travel; motor torque feedback through the final drive (3.31) to the rear wheels; brake pressure through the brake gain; road heights zero on flat ground. Start from measured speed, yaw rate and ride heights. Three modes: open loop; open-loop steering with speed tracking; and windowed restarts from measured states every few seconds.
5. **Compare.** Score car and sim with the same code: BobSim's steady-state and transient metric functions plus `compare_transient_signals` (RMSE, normalized RMSE, max error, bias). Output one report per stage with overlays and a pass/fail table.
6. **Fit.** Only the parameters the current stage owns, bounded to physical ranges and pulled toward measured values, using SciPy least squares (already a BobSim dependency). Report how well each parameter is pinned down; if two move together, fix one from measurement instead of fitting both. Keep held-out runs for checking the fit.
7. **Version and promote.** Each fitted set is a candidate vehicle in `out/<name>/` with its own `bundle.yml`. A passing stage becomes a PR that updates `vehicle/vehicle.yml` and cites the correlation report as the source of each changed value. Every promotion reruns all earlier stages against stored logs, the same way `four-post-acceptance` guards FourPost today.

**How it fits the SIL.** Correlation validates the plant; SIL validates the controller. Feeding logged pedal and steering into SIL and comparing its torque path against the car's logged `TORQUE_PATH` and inverter torque separates controller errors from vehicle-model errors. The SIL plant then runs the promoted parameter set, so a SIL result inherits a stated correlation level. Today the ROS plant uses the 3 DOF model, which shares the same force path and can be scored in the same harness at little extra cost.

**Where LAPDOG fits.** The comparison overlays (car versus sim traces on the same lap) are a natural LAPDOG panel, which also answers the open question in the thread about tying LAPDOG into the tuning loop.

## Open questions

- Which car is the correlation target, Orion as it runs now or Orionwatch, and who owns the Stage 0 measurements that replace the copied BobSim geometry and setup?
- Which sensors will be live on the first correlation day: CSMs, USM wheel speeds, a sprung IMU near the CG, RTK lock?
- Is "the full transient model" DynPy's 14 DOF, as this plan assumes, or the BobLib Modelica tier?
- Does the car run aero? If so, the ride-height reference points need a named source before Stage 3.
- The missing LSD: add it to DynPy upstream, or run Stages 5 to 7 on the Modelica tier as well?
- Is there a flat pad big enough for constant-radius runs and step steer at 20 m/s?
- Do the proposed gates match the measurable goals reviewers asked VMS to show?

## Sources

- [LonghornRacingElectric/lhre](https://github.com/LonghornRacingElectric/lhre) at `ed05b95` (Nov 11): `simulation/README.md`, `simulation/docs/bobsim.md`, `vehicle/vehicle.yml`, `vehicle/bundle.yml`, `docs/can-matrix.md`, `lib/spec/`.
- [BobDyn/BobSim](https://github.com/BobDyn/BobSim) at the lhre pin `f58f932`: `engines/dynpy/` (models, parameters, transient, tire), `simulations/response/` (steady-state, transient and reduced evals).
- LHRE Slack: the #vehicle-modeling-and-software correlation thread (Nov 12), #orionwatch-operations ISO-test discussion (Nov 1), #electronics on CSM and USM status (Oct 20 to Nov 10), #dynamics on sensor needs (Jul 11) and toe compliance (Sep 17).
