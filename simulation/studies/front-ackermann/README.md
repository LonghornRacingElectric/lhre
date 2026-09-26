# front-ackermann

## Question

Can the 2027 Front v19 steering reach the tightest hairpin, and how much
Ackermann should it have? How does that change at the apex, under trail
braking and under throttle?

**This is a screening study.** The steering geometry is Front v19. Mass,
CG, weight split, rack travel, diff direction and "balanced car" are team
estimates for 2027 (2026-09-26). The tire, brake bias, rear geometry,
static toe and compliance are still Orion carryovers or unknown, and the
study varies them. It gives the direction and the sensitivities. See
[Check before design freeze](#check-before-design-freeze).

## Method

**Why not a lap-sim sweep.** The BobSim reduced model gives both front
wheels one steer angle. It builds the GGV at zero yaw rate, so both front
wheels see the same path angle. Its tire has no force peak. A lap-sim
sweep therefore cannot see Ackermann.

**Model.** `run.py` solves a quasi-steady corner at constant radius with
four wheels:

- Each wheel sees its own path angle (yaw rate × wheel position).
- Each tire uses the MF52 lateral force from the `.tir` file, through
  BobSim `_5_App/tire_eval.py`. The fit has a force peak, so a slip-angle
  mismatch between the two front tires costs grip.
- Load transfer comes from the body-frame acceleration, because the track
  and the wheelbase are fixed to the body. Lateral transfer `m·a_y·h` is
  split front to rear by LLTD. Longitudinal transfer is `m·a_x·h/L`.
- The script solves force balance and yaw balance for body slip and steer.
  A bisection on lateral g finds the limit.
- It keeps only stable states: each axle's lateral force must still rise
  with slip.

**Balanced car.** The nominal LLTD is the one that gives the most lateral
g at R = 15 m, where both axles saturate together. Ackermann has almost no
effect at 15 m, so LLTD alone sets the balance there. The result is 33.9%
front. The study also runs ±10 points.

**Longitudinal cases.** A constant tangential acceleration is added. The
total longitudinal force is a third unknown, so the solve also balances
the x axis. Each front wheel's force acts through its steer angle.

- **Trail braking.** Brake force is split by the front bias and is equal
  left and right on each axle (equal hydraulic pressure).
- **Regen through the diff.** The rear share of braking goes through the
  diff, with its coast lock.
- **Throttle.** Drive force goes to the rear through the diff. The LSD uses
  the BobLib `Differential1D` law: the left/right torque difference is
  `0.85 · (2 · T_preload + λ · |T|)`, and it goes to the slower inner
  wheel.
- Three diffs: open, the Orion tune (20 Nm, 0.35 drive, 0.15 coast) and
  the planned Drexler direction (5 Nm, 0.60 drive, 0.35 coast). The planned
  values are illustrative: minimal preload and a 30°/45° ramp pair, where
  a lower ramp angle gives more lock.
- A wheel that needs more longitudinal force than it can give is locked or
  spinning. That state does not count. Limits set by wheelspin are flagged.
- Two combined-slip tire models bracket the result: the friction ellipse
  and a normalized-slip model built from the pure MF52 curves.

**Steering lock and linkage.** BobSim `kin_py` solves inner and outer
roadwheel angle against rack travel from the Front v19 hardpoints
(`2027_FrontV19.shk`, `FRONT SUSPENSION` block, SHA-256 `361e76f3…e467ae02`).
The script also solves new tie rod outer points and rack pickups that
reach the hairpin steer at 90% of rack travel, with a target Ackermann and
zero bump steer.

**Inputs.**

| Input | Value | Source | Varied |
| ----- | ----- | ------ | ------ |
| Mass with driver | 263.1 kg (430 + 150 lb) | team 2027 estimate | ±10 kg |
| CG height | 0.279 m (11 in) | team 2027 estimate | ±20 mm |
| Front static weight | 46% | team 2027 estimate | ±3 points |
| LLTD, front | 33.9% | balanced at 15 m | ±10 points |
| Rack travel | 31.75 mm (1.25 in) each way | team | – |
| Static camber | 0° in the model | team default is −1° | −1° |
| Static toe | 0° | team default | 1.0° in to 1.0° out |
| Diff | see above | team direction | three diffs |
| Brake bias, front | 84% | Orion carryover | 70%; regen |
| Tire | `16x7p5_10_12psi`, LMUY = LMUX = 0.623 | Orion carryover | LKY 1, 0.623 |
| Rear geometry, wheelbase, tracks | 1.549 m, 1.245 / 1.212 m | Front v19, Orion rear | – |

Static camber stays 0° in the model because the tire fit has no camber
thrust (PHY3 = PVY3 = PVY4 = 0). The −1° case changes the gains by less
than 0.3 points.

**Sweep.** The Front v19 curve is compared with constant Ackermann from
−50% to +100% in 25% steps, and in 10% steps for the toe cases. The apex
runs at R = 3.5, 4.5, 6, 8 and 15 m (CG path radius). A 9 m
outside-diameter hairpin puts the tightest legal CG path near 3.5 to
3.7 m.

Ackermann % uses the cotangent convention:
`100 · (cot δ_outer − cot δ_inner) · L / t`.

## Result

![Steering geometry](ackermann_curves.png)

**Steering lock (read this first).**

- Front v19 is parallel steer (+1%). At 31.75 mm of rack it steers 22.0°.
- At the grip limit the car needs 28.9° of mean steer at R = 3.5 m and
  20.7° at 4.5 m. Its tightest CG radius at the limit is 4.3 m.
- At walking speed the rear axle center turns on 3.83 m and the outer
  front wheel center on 4.13 m, against a 4.5 m outside boundary.
- So with 1.25 in of rack, Front v19 cannot hold a minimum hairpin at the
  grip limit. It must slow below the limit in every tight hairpin. This
  costs more than any Ackermann choice.
- The cause is the long steering arm: the tie rod outer point is 82 mm
  from the kingpin axis. Holding R = 3.5 m with this arm needs 40.8 mm of
  rack.

**Linkage options.** Each option reaches its own hairpin steer demand at
90% of rack travel (28.6 mm), which leaves 10% for driver corrections. The
rack pickup moves to keep bump steer at zero (tie rod aligned with the
wishbones' instant center).

| Option | Tie rod outer move | To wheel center plane | Arm to kingpin | Rack pickup move | Toggle margin at inner lock |
| ------ | ------------------ | --------------------- | -------------- | ---------------- | --------------------------- |
| Front v19 | – | 51 mm | 82 mm | – | 67° |
| 0% | 24 mm rearward, 6 mm outboard | 46 mm | 57 mm | 5 mm outboard | 58° |
| +50% | 15 mm rearward, 28 mm outboard | 24 mm | 70 mm | 9 mm outboard, 1 mm down | 41° |
| +70% | 11 mm rearward, 40 mm outboard | 12 mm | 79 mm | 11 mm outboard, 2 mm down | 35° |

- Pro-Ackermann needs less mean steer, because the outer wheel steers
  less: 25.2° for +50% against 28.9° for parallel.
- With a front rack, Ackermann moves the tie rod point outboard, toward
  the wheel. +70% is likely not buildable. +50% needs a CAD check.
- At full lock, bump toe grows to about ±0.6° to ±0.8° over ±25 mm on
  every option (Front v19: ±0.3° to ±0.45°).
- The other levers are more rack travel and moving the rack behind the
  axle. This study did not map them.

![Apex grip and trail-braking grip against Ackermann](grip_vs_ackermann.png)

**Apex.** Change in max lateral g compared with Front v19. Each cell shows
the nominal value, then the range over 12 cases (two tire stiffness
scales, both slip signs, three LLTDs). This assumes the steering can reach
the angle.

| R | +50% | +75% | +100% | Time per 180° turn, +75% |
| - | ---- | ---- | ----- | ------------------------ |
| 3.5 m | +8.5% (+3.3 to +10.4) | +10.1% (+2.7 to +12.7) | +10.0% (+1.7 to +12.6) | −106 ms (−29 to −134) |
| 4.5 m | +3.1% (−0.6 to +4.6) | +3.4% (−1.1 to +5.4) | +2.9% (−1.7 to +5.4) | −42 ms (+14 to −66) |
| 6 m | +0.5% (−0.4 to +1.4) | +0.2% (−0.7 to +1.7) | 0.0% (−0.9 to +1.7) | −3 ms (+10 to −24) |
| 8 m | −0.2% (−0.3 to +0.5) | −0.3% (−0.4 to +0.6) | −0.4% (−0.5 to +0.6) | +5 ms (+6 to −10) |
| 15 m | −0.1% (−0.1 to +0.1) | −0.1% (−0.1 to +0.2) | −0.2% (−0.2 to +0.2) | +3 ms (+3 to −4) |

- Front v19 is front-limited at 3.5 m and 4.5 m. More Ackermann moves the
  car to rear-limited. At 4.5 m, +75% and +100% are already rear-limited.
- Once the rear limits, more Ackermann stops paying. In open corners the
  balanced car is rear-limited, and pro-Ackermann costs 0.1% to 0.4%.

**First-principles check.** The front axle wants the toe difference
`Δθ + α_in − α_out`. Δθ is the difference between the two front wheels'
path angles, which rear slip makes smaller than the no-slip `L·t/R²`. Each
α is the slip angle that maximizes that tire's force weighted by its yaw
lever, `L·cos δ ± (t/2)·sin δ`. For a front-limited car this gives the
optimum:

| R | Front-limited optimum | Solver optimum (10% steps) |
| - | --------------------- | -------------------------- |
| 3.5 m | 87% | 90% |
| 4.5 m | 86% | 70%, rear-limited |

At 3.5 m the car is front-limited and the two agree. At 4.5 m the rear
limits first, so the car takes less Ackermann than the front wants.

**Trail braking at 84% bias.** Change compared with Front v19 at the same
braking, normalized-slip / ellipse tire.

| R, braking | +25% | +50% | +75% | +100% |
| ---------- | ---- | ---- | ---- | ----- |
| 3.5 m, 0.3 g | −0.2 / −0.2% | −0.7 / −0.7% | −1.7 / −1.7% | −3.0 / −3.0% |
| 3.5 m, 0.5 g | +5.0 / +4.7% | +4.6 / +4.2% | +3.6 / +3.2% | +2.3 / +2.0% |
| 4.5 m, 0.3 g | −0.5 / −0.5% | −1.1 / −1.1% | −1.8 / −1.7% | −2.5 / −2.4% |
| 4.5 m, 0.5 g | −0.5 / −0.5% | −1.1 / −1.1% | −1.9 / −1.8% | −2.6 / −2.5% |

- In the balanced car, braking puts load on the front and takes it off
  the rear, so the rear limits. More Ackermann then costs grip.
- The only braking case that gains is 3.5 m at 0.5 g, and there +25% to
  +50% is best.

**Throttle.** Change compared with Front v19 at the same drive, ellipse
tire. "Spin" means a rear wheel sets the limit.

| R, drive | Open diff | Orion LSD | Planned LSD |
| -------- | --------- | --------- | ----------- |
| 3.5 m, 0 g | +6.9 / +7.0 / +6.4% | +9.7 / +11.3 / +11.3% | +10.0 / +11.6 / +11.6% |
| 4.5 m, 0 g | +0.3 / 0.0 / −0.3% | +3.6 / +4.1 / +4.0% | +3.6 / +4.0 / +3.9% |
| 4.5 m, 0.2 g | +1.0 / +1.2 / +1.1% | spin | spin |

Each cell is +50% / +75% / +100%.

- The LSD's locking torque goes to the slower inner rear, which is an
  understeer moment. The car becomes front-limited, and Ackermann is worth
  more. The planned high drive lock makes this stronger than the Orion
  tune.
- At 0.2 g with an LSD, and for Front v19 at 3.5 m with an open diff, a
  rear wheel spins. Those points say nothing about Ackermann.

**Regen through the diff.**

- At 4.5 m the car is mostly rear-limited, and pro-Ackermann costs up to
  2.6%. Only the Orion diff at 0.5 g and 84% gains there, by 0.3% to 1.8%.
- At 3.5 m and 0.5 g, +50% gains 1.8% to 9.3% and +75% gains 0.8% to
  10.9%, depending on the diff and the front share.
- With the planned diff's higher coast lock (0.35), +50% is best at 3.5 m
  and 0.5 g, and pro-Ackermann costs grip at 0.3 g.

**Static toe.** Best Ackermann in 10% steps. The best gain is the same at
every toe, so toe replaces Ackermann and does not add grip.

| Total toe-out | 3.5 m best | 4.5 m best |
| ------------- | ---------- | ---------- |
| −1.0° (toe-in) | +100% | +100% |
| −0.5° | +90% | +90% |
| 0° | +90% | +70% |
| +0.5° | +80% | +60% |
| +1.0° | +70% | +50% |

1° of toe-out replaces about 10 Ackermann points at 3.5 m and about 20 at
4.5 m. Toe-out also costs straight-line scrub, tire heat and darting,
which are not in this model.

**Mass, CG and camber.** Mass ±10 kg, CG ±20 mm, front weight ±3 points
and −1° camber change the gains by less than 0.4 points. +75% stays best
at 3.5 m. At 4.5 m, +50% or +75% is best.

**Michigan 2019 endurance.** BobSim's minimum-curvature line depends only
on the track. It is 1989 m long, with 42 corners under 15 m, 8 under 6 m,
and a tightest radius of 4.5 m. For each corner the script adds arc time
at its minimum radius, plus the speed carried onto the next straight and
into the braking zone. The low value uses a 1.21 g exit and a 1.40 g
entry. The high value uses 0.4 g and 0.5 g.

| Linkage | Time saved per lap vs Front v19 |
| ------- | ------------------------------- |
| +50% | 0.16 to 0.30 s |
| +75% | 0.13 to 0.21 s |
| +100% | 0.07 s |

This is an estimate. It uses the apex results only, and it assumes the
steering can reach every corner.

**Screening result.**

1. **Fix the steering lock first.** With 1.25 in of rack, Front v19 cannot
   hold a minimum hairpin at the limit. Get more rack travel, or a
   shorter steering arm (tie rod point about 57 to 70 mm from the kingpin
   axis).
2. **Aim for about +50% Ackermann at hairpin steer, measured with 0°
   static toe.** It gives the most time per lap on Michigan and loses the
   least under trail braking. The apex alone favors +75% to +90% at 3.5 m.
   Avoid +100%.
3. **Set Ackermann and static toe together.** If packaging allows less
   than +50%, 1° of toe-out makes up about 10 to 20 points, at a
   straight-line cost.
4. **The diff tune moves the answer.** A high drive lock makes more
   Ackermann worth more on exit. A high coast lock makes it worth less
   under regen.

**Drag.** At 80% of the limit and R = 3.5 m, the drive force to hold speed
is 262 N for Front v19, 148 N for +50% and 112 N for +75%. Less front slip
mismatch means less tire drag. At 8 m the difference is under 6 N.

**Across events.**

- Acceleration: no effect.
- Skidpad (R = 9.125 m): between the 8 m and 15 m results, so within 0.6%.
- Autocross: use the Michigan method on the autocross course.

**Confidence.** The limits are in the scope and the inputs, not in the
math.

- **Math.** Solves converge to a residual under 1e-7. The closed-form
  front optimum matches the solver where the front limits. An independent
  implementation matches the braking, throttle and toe results to
  0.001 g.
- **Scope.** The model is quasi-steady. It cannot show power-on or
  trail-brake rotation beyond the steady state. It has no steer camber,
  compliance steer, aligning moment or yaw acceleration. The lap estimate
  puts each corner at its minimum radius.
- **Inputs.** The tire is a raw TTC fit with an unvalidated 0.623 grip
  scale. The gain changes by up to 5× across the cases. The
  balanced-car LLTD and the planned diff values are assumptions.
- **What holds in every case:** Front v19 with 1.25 in of rack cannot hold
  a minimum hairpin at the limit, +100% is never the best choice, open
  corners gain nothing, and toe trades one-for-one with Ackermann.

## Check before design freeze

1. **Geometry source.** The Front v19 SHK and the hardpoint tracker
   disagree. Decide which is the source of truth, and check the −4.85°
   caster.
2. **Steering lock.** Pick more rack travel or a shorter steering arm.
   Check tire, wheel and body clearance at full lock, and the toggle margin
   of the inner wheel.
3. **Static toe and compliance.** Set the static toe together with the
   Ackermann. Measure toe change under load.
4. **Tie rod and rack packaging.** Check the chosen tie rod outer point
   against the wheel and brake in CAD, and move the rack pickup to keep
   bump steer at zero.
5. **Brake bias and regen.** Get the 2027 hydraulic bias and the regen
   torque commanded under braking.
6. **Diff tune.** Get the Drexler ramp angles, plate count and preload.
7. **Tire.** Validate the grip scale on track.

Rerun the study when these are known.

## Provenance

Front v19 steering hardpoints, team 2027 mass and CG, balanced LLTD, Orion
rear and tire.

```json
{
  "study": "front-ackermann",
  "bobsim_sha": "4da577af1b04d86c53706eb6e80fb0064f71cee6",
  "vehicle_sha256": "3ab02bbf3e573aad0330bc37ce40fd254090d33dabd3760462c51da74e30afe8",
  "utc": "2026-09-26T18:27:56+00:00"
}
```
