# BobSim correlation plan

Status: draft for the Base design review. Answers from Nov 14 and Nov 15 are folded in.

Correlate BobSim's high-fidelity tier, the BobLib Modelica model, to Orionwatch one subsystem at a time, scored with the wheel speeds, the CSM corner boards, the IMU and the GPS. Fix the inputs and the known parameters first, then test one subsystem per stage, and keep laps for validation only. A failed gate leads to a revision of the Modelica implementation, not to a fit of parameters until the traces agree. Every stage has a numeric gate no tighter than the car's own run-to-run scatter, and every test the sensors cannot score is marked with what would unlock it.

## What changes from the original plan

The original idea holds: a fixed spec list, simple to complex, steady state before transient, run on the car then in the sim, fix the gap, repeat. Eight changes make it work in practice.

1. **Correlate the Modelica model through a replay of the car's own inputs.** BobLib's `VehicleFMI` takes handwheel angle and pedal positions, the same entry point lhre already builds for the autonomy plant. Logged inputs drive it, and its outputs are scored against the car.
2. **Add a Stage 0 with no driving.** `vehicle/bundle.yml` marks the hardpoints, springs, toe, camber, bar rates and brake bias as copied from BobSim's own car (status `assumed`), and the masses and inertias have no named source. Orionwatch's values are all set by its design, so Stage 0 is mostly bookkeeping: replace every copied value with Orionwatch's own, so real errors don't hide inside fitted numbers.
3. **Add a sensor check before any comparison.** On the UTA drive days the CSM and USM boards never reported. The plan assumes the wheel speeds, the CSMs, the IMU and the GPS are live, and each channel gets a known-input check before its data counts. The USM firmware has two known limits that this check has to cover (see [USM firmware limits](#usm-firmware-limits)).
4. **Check what can be checked quasi-statically before full replay.** Where a stage allows it, measured states go into a quasi-static check built from BobLib's K&C results (for example, measured lateral acceleration into roll and load transfer). Then the whole vehicle replays. See [Pass A and Pass B](#the-staged-test-plan) for what exists today.
5. **Vertical, then longitudinal, then lateral.** Ride and pitch behaviour sets the normal loads that every tire force depends on, and speed and drag set the operating point of every lateral test, so they are frozen first.
6. **Replay the driver's measured inputs instead of asking for perfect maneuvers.** Driver variation becomes an input, not an error, and repeats only need to measure the car's scatter.
7. **Mark what the sensors can't score.** The GPS covers path, segment times and absolute speed under wheel slip. Body sideslip also needs heading. If the GPS has one antenna, it gives course but not heading, so sideslip comes from GPS course fused with IMU yaw rate over short windows. Gates the sensors still can't score are listed with what unlocks them, rather than quietly dropped.
8. **A failed gate changes the model, not the fit.** Fitting parameters until the traces agree overfits: the wrong parameter absorbs a missing physical effect, and the error comes back in the next stage. A parameter changes only with a measurement or a physical reason behind it. Otherwise, a failed gate leads to a revision of the Modelica implementation.

Full laps stay a hold-out: they validate the frozen model and are never used to tune it. The gate for every metric is the larger of an engineering tolerance and twice the measured run-to-run spread of that metric on the car.

## What the Modelica model holds

The target is BobLib's `VehicleSim` and its input-driven twin `VehicleFMI` (BobLib `75b6e03`, pinned by BobSim `f58f932`), compiled with our records by `make records`. It is a MultiBody model with 14 core mechanical DOF: 6 for the chassis, and wheel speed and vertical travel for each of the 4 wheels. The frame model that lhre uses (`FrameCompX`) adds a 15th, frame twist. Driveline and tire-lag states come on top.

| Part | How the model does it | DOF or states | Seen on the car by |
| --- | --- | --- | --- |
| Body | Sprung mass in two halves joined by a torsion spring and damper (`FrameCompX`) | 6, plus 1 frame twist | IMU; CSM ride heights |
| Suspension | Double wishbone with bellcrank and anti-roll bar front and rear, as real linkages; spring and damper are lookup tables, not single rates | 4 (vertical travel per wheel) | CSM sus pots and corner accels |
| Steering | Rack and pinion driven by handwheel angle through a filter | Driven input | Steering column angle |
| Wheels | Spin on rigid hubs; six-axis wheel compliance exists but is off | 4 spin | USM wheel speeds |
| Tire | MF5.2 combined slip with transient slip (relaxation length). Vertical compliance is a one-sided penalty contact with the road (`RoadGround`), not a spring in the wheel. See [Tire assumptions](#tire-assumptions). | Lag states per tire | CSM strain-gauge loads; IMU transients |
| Driveline | Motor, fixed ratio, clutch LSD, halfshaft stiffness and damping, inboard rear brakes | Rotor, LSD and shaft states | Inverter speed and torque; rear left versus right wheel speed |
| Aero | CFD map of loads against ride height | None | Coastdown by wheel speed; CSM ride heights |
| Atmosphere and road | Constant density, flat road | None | Logged by hand |
| Driver and VCU | Takes handwheel angle and accelerator and brake pedal; BobLib's own VCU turns pedal into torque | None | Pedals, torque command and feedback |

BobSim already scores this model with `standard-eval-steady-state`, `standard-eval-transient`, `standard-eval-ramp-steer` and `standard-eval-four-post`, and BobLib has K&C rigs (`KnCSteerable`, `KnCLocked`). The plan runs the same metric code on car logs, so both sides are scored identically.

**What it fixes from the first draft.** The clutch LSD, tire relaxation, nonlinear spring and damper tables, frame torsion and true linkage kinematics are all in this model. The DynPy gaps the first draft listed are closed.

**Frame torsion stays in.** `vehicle.yml` sets `body.torsional_stiff_n_m_per_rad: 300000` with no source. The real frame is about 900 N·m/deg (51 566 N·m/rad), so the model is about 5.8 times too stiff. At 900 N·m/deg, a two-half frame estimate moves the front share of elastic lateral load transfer by 1 to 2.5 percentage points, about one bar change, and twist is 5 to 10% of body roll. That estimate uses typical FSAE roll stiffnesses, not Orionwatch's. So `FrameRigid` (14 DOF) is not good enough: keep `FrameCompX`, set the measured stiffness in Stage 0, and check twist in Stage 5. Every BobSim result so far uses the 300 kN·m/rad value, so results that depend on load transfer balance may move.

**Gaps that remain.** If a stage fails with its parameters set from measurement, the cause is likely one of these, and the fix is a model change, not a fudge factor:

- **Pedals, not torque.** `VehicleFMI` takes only handwheel angle, accelerator pedal and brake pedal. It does not take torque command or brake pressure, so a replay exports pedal positions, and torque is an output to compare. A replay also scores BobLib's VCU against Orionwatch's. For now, replay through the pedals, and give BobLib's VCU the same power cap the car ran that day. Orionwatch's VCU runs a 70 kW cap today, and logs reach 72.9 kW at p99. If the torque paths still disagree, set both VCUs to a plain upper bound at the 80 kW rules limit. A variant that takes measured motor torque and brake pressure (BobLib work), or the SIL VCU in the loop, can come later. Stage 4 compares torque before anything else.
- **Starting state.** lhre sets `modelica_initialization: legacy`, so the model settles from a pose solved for no particular car. `VehicleFMI` takes only a starting speed (`initialVel`). It has no starting yaw rate, lateral velocity, roll or ride heights, so a replay must begin on a straight at steady speed. A replay that starts mid-corner needs those starting values and a solved pose for our record (BobLib work, per `simulation/docs/bobsim.md`).
- **Rigid links and hubs.** No bushing or upright compliance, and wheel compliance is off and uncalibrated. Toe compliance was already raised in #dynamics.
- **Aero is constant.** The ride-height reference points sit above both CFD grids, so aero clamps at the grid corner, as in DynPy. Orionwatch runs aero, but the only aero maps vary with pitch, not with ride height. Removing the aero package for the correlation days may be possible. That takes aero out of Stages 2 to 7, and a later aero stage adds it back.
- **Fixed air density, no wind.**
- **Tire contact not wired to the vehicle file.** `PartialChassis` does not set c and d on the `RoadGround` contacts, so they keep BobLib's defaults (100 000 N/m, 750 N·s/m). The `vehicle.yml` values (98 947 N/m, 115.844 N·s/m) are not used. The stiffness is close; the damping is about 6.5 times higher, which over-damps wheel hop. Stage 2 needs these wired from the vehicle record (BobLib work), or set as FMU parameters if the compiler keeps them tunable (not tried).
- **Slip uses the unloaded radius.** Slip ratio uses R0 = 0.2045 m from `vehicle.yml`, even though the contact point sinks into the road. Car and model must use the same radius when slip ratios are compared (Stages 4 and 7).
- **Tire file with no source.** See [Tire assumptions](#tire-assumptions). Grip is scaled per session (see the staged plan).

One parameter is worth a quick check before any testing: wheel inertia is 0.02 kg·m² per corner, which looks low for a 10 inch wheel and tire.

### Tire assumptions

Both axles use one file, `vehicle/tires/16x7p5_10_12psi.tir`, byte-identical to BobSim's template. Its header says PAC2002, a 16x7.5 tire at 82 737 Pa (12 psi), tested at 12 m/s. It names no manufacturer, test or owner, and `bundle.yml` marks its source and license as unavailable. What follows from that file and from BobLib's MF5.2 code:

- **Raw rig grip.** Peak friction is about 2.6 longitudinally and 2.4 laterally at the nominal 650 N load. All scaling factors are 1. That is why a per-session grip scale is needed.
- **Copied shape factor (inference).** The longitudinal shape factor equals the lateral one exactly (1.53041), which suggests it was copied, not fitted.
- **No pressure or temperature effects.** All pressure terms are zero, and MF5.2 has no temperature terms. Pressure and temperature changes only show up through the scale factors.
- **Symmetric tire.** All shift terms are zero: no ply steer, no conicity, and no camber thrust at zero slip angle. Camber changes only peak grip, cornering stiffness and curvature.
- **No rolling resistance from the tire.** The rolling-resistance coefficients are zero, so Stage 3 rolling loss has no home in the tire.
- **No speed dependence** in the forces.
- **Default relaxation lengths.** The relaxation coefficients are zero, so `TransientSlip` uses its 0.5 m defaults in both directions, independent of load and speed.
- **Silent extrapolation.** The fit covers slip ratio ±0.15, slip angle ±15°, camber 0° to +4° and load 100 to 1800 N. Below 100 N, forces scale linearly from the 100 N value. Above 1800 N, nothing clamps, so the formula extrapolates. Slip is clamped only at slip ratio 2.0 and slip angle 1.2 rad.
- **Road friction scale is 1.** The road is flat, so the `RoadGround` friction scale stays at 1. If a road surface is ever turned on, that factor scales the whole force, cornering stiffness included.

DynPy ports the same equations from the same file, with the same minimum-load clamp only.

### What BobSim can run today

From the FMU runner at BobSim `f58f932` and `VehicleFMI` at BobLib `75b6e03`:

| Need | Today | Detail |
| --- | --- | --- |
| Drive `VehicleFMI` with logged steering and pedals | Yes | The runner's `_input_profiles` takes time and value arrays for any FMU input, interpolates linearly and holds the end values. |
| Start at the measured speed | Probably | `initialVel` sets the starting speed and spins the wheels to match. It is an ordinary parameter, so `_parameters` should set it. Not tried. |
| Start from a measured mid-maneuver state | No | No parameter for yaw rate, lateral velocity, roll or ride heights. The runner can only restore its own initialization snapshot. |
| Windowed restarts | No | Needs the row above. |
| Score car logs with the BobSim evals | Partly | The steady-state, transient, ramp-steer and four-post evals and `compare_transient_signals` read sim output. Loading car logs into them is lhre study work. |
| Quasi-static subsystem checks | Partly | The K&C and four-post rigs run fixed built-in protocols. They give stiffnesses and gradients, not replays of measured motion. The four-post rig is quasi-static, with no body inertia and no damping. |

## What the car can measure

With wheel speeds, the CSMs, the IMU and the GPS live, every model DOF can be scored, frame twist only as a check and body sideslip through GPS and IMU fusion. The critical bus (steering, pedals, torque, brake pressure) did log at UTA, so replay inputs are there. Rates are from `lib/spec`.

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
| Frame twist | Front roll minus rear roll from the four ride heights | Check only. The signal is about 0.1° at 1.5 g, near the ride-height resolution, and uneven tire deflection gives a signal of the same size. Measure the stiffness on a rig instead. |
| Unsprung acceleration (3 axes) | USM accel, in the same frame as wheel speed (333 Hz frames, 104 Hz samples) | Direct. The accelerometer range is ±4 g, so it can clip on a curb. See [USM firmware limits](#usm-firmware-limits). |
| Absolute speed | GPS | Direct, also while all four wheels slip. |
| Position and path | GPS | Direct. |
| Body sideslip | GPS course and IMU yaw rate | Estimate over short windows. Direct if the GPS gives heading (two antennas). |

**Inputs for replay.** Steering column angle (333 Hz), pedal positions, inverter torque command and feedback (333 Hz), the VCU torque path (100 Hz), and front and rear brake pressure (333 Hz) are all on the critical bus.

**Not on any bus, so logged by hand each session:** tire pressures hot and cold, tire surface temperatures, tire set and heat cycles, track and air temperature, setup sheet, ballast, driver.

### What these sensors can't score, and what would

| Blocked | Stages hit | What unlocks it |
| --- | --- | --- |
| Body sideslip as a direct measurement | 5, 7 | A GPS with heading (two antennas), or an optical ground-speed sensor. Until then, the GPS and IMU estimate scores the gates. |
| Road-wheel angle and steering compliance | 0, 5, 6 | A rack position sensor, or toe measured under load in Stage 0 |
| Frame twist as a number | 5, 7 | A torsion test in Stage 0 sets the stiffness. Strain gauges on the frame would measure twist directly. |
| Wind | 3 | An anemometer, or coastdowns on still-air days in both directions |

### USM firmware limits

The USM firmware from last season ([lhre-2026](https://github.com/LonghornRacingElectric/lhre-2026) at `d981057`) sends one frame per corner: 3-axis unsprung acceleration and wheel speed, IDs 1026 to 1029, 333 Hz, on the Data Acq bus to the VCU and the Pi. lhre's `lib/spec/messages/usm.textproto` has the same frames. Two limits in that firmware affect correlation.

- **Accelerometer.** [`imu.c`](https://github.com/LonghornRacingElectric/lhre-2026/blob/d981057/USM/firmware/usm/imu.c) sets the ASM330 to 104 Hz and ±4 g. The 333 Hz frames repeat samples, so the real accel bandwidth is about 50 Hz. That is enough for wheel hop. A curb or a plank may push the unsprung mass past 4 g and clip the signal. Set the range to ±8 g or ±16 g before Stage 2.
- **Wheel speed.** [`wheel_speed.c`](https://github.com/LonghornRacingElectric/lhre-2026/blob/d981057/USM/firmware/usm/wheel_speed.c) times the interval between magnet crossings with a 1 ms tick, and the wheel has 6 magnets. At 20 m/s on a 0.2 m radius, a crossing comes about every 10 ms, so one reading may be off by about 10%. That is wider than the Stage 7 gate on rear left-versus-right speed, and too coarse for slip ratio in Stage 4. Time the crossings with a microsecond timer.
- **Missed crossings at speed (not tested).** The firmware smooths the raw field with an EMA (`WS_EMA_ALPHA = 0.05`) before the crossing check. At speed, the magnet frequency may be well above the filter cutoff, so the field may stay under the 3.0 hysteresis and crossings may be lost. The Stage 1 check against GPS up to top speed finds this.

## The staged test plan

Nine stages, each freezing what later stages depend on. Gates are proposed starting values for the Base review to challenge; each widens to twice the car's own run-to-run spread if that is larger.

The test surface is a Class B surface. It is not flat, but it is consistent. Run every lateral test in both directions so that the slope cancels. The run-to-run spread rule widens a gate where the surface adds scatter.

| Stage | Car test | Live sensors used | Isolates and checks | Gate (proposed) |
| --- | --- | --- | --- | --- |
| 0. Set Orionwatch's values | Take hardpoints, springs, dampers, bars, steering ratio, masses and inertias from Orionwatch's design. Check the few that the build can change: corner weights with driver, static ride heights, toe and camber. Measure frame torsional stiffness on a rig (suspension locked, one corner jacked) to confirm the 900 N·m/deg figure. | CSM ride heights and sus pots at rest | Replaces every `assumed` value in `bundle.yml` with Orionwatch's value and its source. Nothing is fitted. | Static loads within 1% per corner; static ride heights within 1 mm; camber and toe gain within 10% of BobLib's K&C rig |
| 1. Check the sensors | Static tilt for IMU orientation; jack each corner to known heights; known loads on the strain gauges; known steering angles; a measured roll-out for wheel-speed radius, then a constant-speed run checked against GPS up to top speed; one sharp tap for timing across USM, CSM and IMU | All | Calibration tables and time offsets for every channel. Nothing in the model changes. | Each channel within its rated accuracy; cross-board timing under 5 ms; wheel speed within 1% of GPS speed at every speed tested |
| 2. Vertical | Push-and-release heave and pitch at rest; slow drive over a plank or curb | CSM ride heights, sus pots, corner accels; USM accel | Damper tables, tire contact stiffness and damping (needs the `RoadGround` wiring) | Heave and pitch frequency within 5%; damping ratio within 0.05; wheel-hop frequency within 10% (USM accel; check for clipping at ±4 g on the plank) |
| 3. Longitudinal, steady | Coastdowns both directions with zero torque; constant-speed runs at 3 to 4 speeds | Front wheel speeds; IMU ax | Drag area, rolling resistance, driveline loss | Drag area within 5%; speed trace RMS under 0.3 m/s |
| 4. Longitudinal, transient | Straight-line launches at stepped torque limits; stops at stepped brake pressures | Wheel speeds (slip ratio); GPS speed; IMU ax; CSM pitch and loads | First the pedal-to-torque path, then longitudinal tire stiffness and peak grip, brake gain, pitch gradient (checks anti geometry) | Motor torque trace NRMSE under 5%; peak ax within 5%; pitch gradient within 10%; ax trace NRMSE under 10%; speed under full slip within 0.3 m/s of GPS |
| 5. Lateral, steady | Constant radius with slowly rising speed, both directions; slow ramp steer at constant speed | IMU ay and yaw; CSM roll and loads; wheel speeds; GPS | Linear range first: front and rear cornering stiffness, roll stiffness split, frame twist with the measured torsion stiffness held fixed. Then the limit: front and rear peak lateral grip. | Understeer gradient within 0.5 deg/g; roll gradient within 10%; lateral load transfer within 10% (strain gauges); max ay within 3%; sideslip gradient within 0.5 deg/g (GPS and IMU estimate); front minus rear roll within 0.1 deg/g |
| 6. Lateral, transient | Step steer and sine steer at 15 and 20 m/s and 5 deg (the speeds BobSim's TransientEval uses); a swept or random steer for frequency response | IMU yaw and ay; steering angle | Yaw inertia if not measured; tire relaxation length, which this model now has | Yaw and ay steady gain within 5%; response time within 20 ms; overshoot within 5 points; frequency-response gain within 1 dB and phase within 10 deg up to 2 Hz |
| 7. Combined | Brake in turn, lift-off in turn, power-on exit | IMU; rear left and right wheel speeds; CSM loads; GPS | Combined-slip tire terms; LSD preload and locking | Yaw rate and ay trace NRMSE under 15%; rear wheel-speed difference within 10% (needs the wheel-speed fix); sideslip trace NRMSE under 20% (GPS and IMU estimate) |
| 8. Laps (hold-out) | Autocross and endurance laps | IMU; wheel speeds; GPS | Nothing. Validation only. | Overlapping GG envelopes; minimum corner speeds within 3%; lap time within 2% (timing beam); segment times within 3% (GPS) |

**Each stage runs in two passes.** Pass A is a quasi-static check built from BobLib's K&C results: for example, measured lateral acceleration into roll and load transfer, or ride height against measured load. It exists only where K&C rates are enough. Dynamic subsystem replays (dampers, driveline, yaw) need standalone rigs that BobLib does not have, and the tire alone is weak because every input is an estimate. Pass B replays the logged driver inputs through `VehicleFMI`, starting on a straight at steady speed. Pass A localizes an error to one subsystem where it can, before Pass B can blur it across all of them.

**Why this order, where it differs from steady-state-then-transient.** Vertical dynamics come early even though they are transient: they set the normal loads every tire force scales with, they barely depend on the tire's lateral behaviour, and they can be tested at walking speed. Longitudinal comes before lateral because speed, drag and slip set the operating point of every cornering test. Within each axis the original rule holds: steady state first, then transient.

**Tire grip is a session parameter, not a car parameter.** Start from the 0.60 to 0.66 scale on TTC peak grip suggested in #orionwatch-operations, but scale peak grip (LMUX, LMUY) separately from cornering stiffness (LKX, LKY), because one blanket factor also changes steering response. Every test day opens with a short reference run on the same skidpad, which sets that day's grip scale.

## The worry points from the Slack thread

All four worry points from the #vehicle-modeling-and-software correlation thread have an owner stage.

| Worry | How the plan handles it | Where |
| --- | --- | --- |
| Turning operating conditions into an equivalent run; tire scaling for degradation and road surface | Log tire set, heat cycles, pressures and temperatures by hand each session. Treat grip scaling as a per-session value set by a daily reference skidpad run, so wear and surface show up as a drift in that value instead of polluting car parameters. Scale peak grip and cornering stiffness separately. Hold pressure and temperature windows fixed for tuning runs. | Stage 5 reference run, every test day |
| Correlating every DOF from one run hides where an error starts | Never fit on a full run. Each stage checks one subsystem, runs Pass A where it exists, and freezes before the next stage. Replays are cut into tests that each start on a straight at steady speed, so drift cannot compound across a lap. Windowed restarts mid-corner come once `VehicleFMI` takes a full starting state. | Every stage, Pass A then B |
| Telling model error from execution error | Three layers. Sim execution: BobLib's own regression baselines, and the same maneuver run on DynPy as a sanity check, catch solver and setup faults. Measurement: Stage 1 calibrations and timing checks. Driver execution: replay uses logged inputs, so a missed maneuver is still a valid comparison. An error counts as model error only if it is larger than the repeat spread and survives all three. | Stage 1, plus a sim self-check before every comparison |
| Driver deviation (law of large numbers feels heavy) | Replaying measured steering, torque and brake inputs removes the need for identical runs. Repeats are only needed to measure the car's own spread: 3 runs per condition and direction is a reasonable start. Steady-state windows are cut automatically with the same settle tolerances BobSim's steady-state eval uses. The autonomy stack could later drive repeatable steer inputs. | Stages 5 to 8 |

## Tooling pipeline

The pipeline is a BobSim study inside `simulation/studies/`, so it inherits the existing `make study` runner, `identity.json` provenance and candidate-vehicle checks instead of building a parallel system.

```text
1 Drive ──> 2 Data acquisition ──> 3 Segment ──> 4 Cleaning
   ^                                                  │
   │ next stage                                       v
8 Advance stage <── pass ── 7 Gate <── 6 Loss <── 5 BobDyn dual
                              │                       ^
                              └─ fail ──> Revise Modelica implementation
```

1. **Drive.** The testing plan dictates the maneuver.
2. **Data acquisition.** Logs stream to the telemetry server. A session sheet records tire set, pressures, temperatures, setup sheet id, ballast, driver and `bundle_id`.
3. **Segment.** Apply a time mask for each test. Each test starts on a straight at steady speed. Steady-state windows use BobSim's own settle tolerances.
4. **Cleaning.** Apply the Stage 1 scalings, correct IMU mounting angles from a static window, remove cross-board time offsets, filter noise and resample to one 100 Hz grid. Derive slip ratios (with the model's radius), tire deflection and ay at the CG.
5. **BobDyn dual.** Replay the driver trace through `VehicleFMI` with the logged handwheel angle and pedals, using BobSim's FMU runner (`_input_profiles`) as lhre's `lhr_sim_fmu` plant already does. Start at the measured speed. A torque-input variant replaces the pedals once it exists.
6. **Calculate loss.** RMS error on each DOF signal, with the same code for car and sim: BobSim's steady-state, transient, ramp-steer and four-post evals, plus `compare_transient_signals` (RMSE, normalized RMSE, max error, bias). Output one report per stage with overlays.
7. **Gate.** Each metric must fall within the larger of its tolerance and twice the car's run-to-run spread.
8. **Advance stage.** Move to the next test in the plan.

**On a failed gate, revise the Modelica implementation.** Find which physical effect is missing or wrong, change the model in BobLib, bump the pin, and replay. A parameter in `vehicle.yml` changes only with a measurement or a physical reason, and the PR cites it. Every model revision reruns all earlier stages against stored logs, the same way `four-post-acceptance` guards FourPost today, so a fix for one stage can't break another.

**How it fits the SIL.** Correlation validates the plant; SIL validates the controller. Feeding logged pedal and steering into SIL and comparing its torque path against the car's logged `TORQUE_PATH` and inverter torque separates controller errors from vehicle-model errors. The autonomy FMU plant runs this same Modelica model, so each correlated model revision carries its correlation level straight into autonomy and SIL results.

**Where LAPDOG fits.** The comparison overlays (car versus sim traces on the same lap) are a natural LAPDOG panel, which also answers the open question in the thread about tying LAPDOG into the tuning loop. Arjun's proposal for the review (export a lap's inputs from LAPDOG, replay through the FMU, overlay a ghost trace, score RMS and peak error per lap and sector) is steps 5 and 6 of this pipeline. The export must be pedal positions, not torque command or brake pressure.

## Blockers

Most severe first.

| Blocker | Kind | Stages hit | Unblocked by |
| --- | --- | --- | --- |
| No logs yet with CSM, USM, IMU and GPS all live | Car data | 1 to 8 | A drive day with all boards reporting |
| USM wheel speed: 1 ms tick, 6 magnets, possible missed crossings | Car data | 4, 7 | USM firmware: microsecond timing (owner open) |
| USM accelerometer clips at ±4 g | Car data | 2 | USM firmware: set ±8 g or ±16 g |
| GPS model, rate and antenna count unknown | Car data | 5, 7 | Name the unit |
| Frame torsion in `vehicle.yml` is 5.8 times too stiff | Model input | 5, 7, every load-transfer result | Stage 0 torsion test, then a `vehicle.yml` change with its source |
| `RoadGround` contact c and d not wired to the vehicle record | Model | 2 | BobLib change, or FMU parameters if tunable |
| No mid-maneuver starting state in `VehicleFMI` | Model | Windowed replays | BobLib change |
| Aero constant because ride-height points sit outside the CFD grid | Model | 3 to 7 | Remove aero for correlation days, or a ride-height map |
| Tire file with no source | Model input | 4 to 8 | A known `.tir`; until then, the per-session grip scale |
| No code loads car logs into the BobSim evals | Tooling | 2 to 8 | The correlation study (steps 2 to 6) |

Possible now: Stage 0, Stage 1 once the boards report, and straight-line replays for Stages 3 and 4 with the coarse wheel speed.

## Open questions

Settled on Nov 14 and 15:

- The model is BobLib's Modelica model, and the car is Orionwatch.
- The model has 14 core DOF (chassis 6, plus wheel speed and vertical travel per wheel). `FrameCompX` adds frame twist, and it stays in.
- The real frame torsional stiffness is about 900 N·m/deg.
- The sensors are wheel speed, the CSMs, an IMU and a GPS.
- Stage 0 values come from Orionwatch's design. They are known before testing.
- The USM sends 3-axis unsprung acceleration with wheel speed (see [USM firmware limits](#usm-firmware-limits)).
- Replay goes through the pedals and BobLib's VCU for now. BobLib's VCU uses the car's power cap. If necessary, both VCUs get a plain 80 kW upper bound.
- A failed gate leads to a revision of the Modelica implementation, not a parameter fit.
- Orionwatch runs aero. The aero maps vary with pitch only. The aero package may come off for the correlation days.
- The test surface is a Class B surface: not flat, but consistent.
- The review asks for concrete deliverables and measurable goals. The numeric gates per stage are those goals.

Still open:

- Where is the IMU: on the sprung mass near the CG, or elsewhere? The gates assume near the CG. Other positions work with lever-arm corrections.
- Which GPS, at what rate, and with one antenna or two? Two antennas give heading, and so a direct sideslip measurement.
- Does the aero package come off for correlation days? If it stays on, the model needs a ride-height aero map and a named source for the ride-height reference points before Stage 3.
- Who changes the USM firmware (accel range and wheel-speed timing) before Stage 1?
- Is the 900 N·m/deg frame figure from FEA or from a test? FEA usually reads stiffer than the built frame.

## Sources

- [LonghornRacingElectric/lhre](https://github.com/LonghornRacingElectric/lhre) at `ed05b95` (Nov 11): `simulation/README.md`, `simulation/docs/bobsim.md`, `vehicle/vehicle.yml`, `vehicle/bundle.yml`, `docs/can-matrix.md`, `lib/spec/messages/` (CSM and USM), `autonomy/ros2/src/lhr_sim_fmu/README.md`.
- [LonghornRacingElectric/lhre-2026](https://github.com/LonghornRacingElectric/lhre-2026) at `d981057`: `USM/firmware/usm/` (`usm_can.c`, `imu.c`, `wheel_speed.c`) and `USM/firmware/Core/Src/app_freertos.c`.
- [BobDyn/BobSim](https://github.com/BobDyn/BobSim) at the lhre pin `f58f932`: `simulations/response/mbd/` (Modelica steady-state, transient, ramp-steer and four-post evals and the FMU runner), `docs/boblib-submodule.md`.
- [BobDyn/BobLib](https://github.com/BobDyn/BobLib) at the BobSim pin `75b6e03`: `README.md`, `RELEASE_READINESS.md`, `BobLib.UsersGuide`, `Experiments/Standards/` (VehicleSim, VehicleFMI, K&C rigs), `Chassis/` (frame, suspension, MF5.2 tire and wheel models, `BaseClasses/PartialChassis.mo`), `Utilities/Mechanics/MultiBody/ContactMechanics/RoadGround.mo`, `Drivelines/`.
- LHRE Slack: the #vehicle-modeling-and-software correlation thread (Nov 12), #orionwatch-operations ISO-test discussion (Nov 1), #electronics on CSM and USM status (Oct 20 to Nov 10), #dynamics on sensor needs (Jul 11) and toe compliance (Sep 17), and Arnav's answers on model, car, sensors, Stage 0, VCU, aero and test surface (Nov 14), and on DOF count, frame torsion and model revision over fitting (Nov 15).
- Notion: Base Design Review, Group 1, Test Correlation breakout (VCU power cap, Arjun's replay proposal, review asks).
