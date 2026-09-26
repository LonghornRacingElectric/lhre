# front-ackermann

## Question

How much Ackermann should the 2027 Front v19 steering have? How much does
it change the car at the apex, under trail braking and under throttle?

**This is a screening study.** The steering geometry is 2027 Front v19.
Mass, CG, LLTD, tire, brake bias, diff and alignment are Orion carryovers,
and the study varies each one. It gives the direction and the
sensitivities. It does not give a 2027 target until the inputs in
[Check before design freeze](#check-before-design-freeze) are known.

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
  with slip. This removes a drift state with 16° to 22° of rear slip.

**Longitudinal cases.** A constant tangential acceleration is added. The
total longitudinal force is a third unknown, so the solve also balances
the x axis. Each front wheel's force acts through its steer angle.

- **Trail braking.** Brake force is split by the front bias and is equal
  left and right on each axle (equal hydraulic pressure).
- **Regen through the diff.** The rear share of braking goes through the
  diff instead of equal hydraulic force, with the diff's coast lock.
- **Throttle.** Drive force goes to the rear through the diff. The LSD
  uses the BobLib `Differential1D` law: the left/right torque difference is
  `0.85 · (2 · T_preload + λ · |T|)`, and it goes to the slower inner
  wheel. An open-diff case sets it to zero.
- A wheel that needs more longitudinal force than it can give is locked
  or spinning. That state does not count. A limit set by wheelspin says
  nothing about Ackermann, so the study flags it.
- Two combined-slip tire models bracket the result. The friction ellipse
  reduces lateral force by `√(1 − (Fx/Fx_max)²)`. The normalized-slip model
  builds combined slip from the pure MF52 curves, so a large slip angle
  also cuts longitudinal capacity. Neither uses the fit's combined-slip
  coefficients, which have no documented source.

**Geometry.** The Front v19 front-left hardpoints come from
`2027_FrontV19.shk`, `FRONT SUSPENSION` block (SHA-256 `361e76f3…e467ae02`).
Positive x points forward, and the origin is on the front axle centerline.
The rear SHK puts its wheel center at x = −1549.4 mm, which confirms the
frame. BobSim `kin_py` solves inner and outer roadwheel angle against rack
travel. `run.py` holds these points as constants and writes the changed
vehicle copy to `out/front-ackermann/vehicle_front_v19.yml`.

**Carryover inputs.** These come from `vehicle/vehicle.yml` (Orion):

| Input | Value | Varied in the study |
| ----- | ----- | ------------------- |
| Mass with driver | 261.1 kg | ±10 kg |
| CG height | 0.280 m | ±20 mm |
| Front static weight | 48.3% | ±3 points |
| LLTD, front | 52.0%, BobSim nominal roll stiffness | 32%, 52%, 62% |
| Brake bias, front | 84% | 84%, 70%; regen through the diff |
| Diff | clutch LSD, 20 Nm preload, 35% drive lock, 15% coast lock | LSD, open |
| Tire | `16x7p5_10_12psi`, LMUY = LMUX = 0.623 | LKY 1, 0.623; two combined-slip models |
| Static toe | 0° | 1.0° in to 1.0° out |
| Camber | 0° | not varied |

**Sweep.** The script compares the Front v19 curve with constant Ackermann
from −50% to +100% in 25% steps, and in 10% steps for the toe cases. The
apex sweep runs at R = 3.5, 4.5, 6, 8 and 15 m (CG path radius). A 9 m
outside-diameter hairpin puts the CG path near 3.5 to 4 m. The
longitudinal, toe and mass cases run at 3.5 and 4.5 m.

| R | Front v19 roadwheel angle at the limit |
| - | -------------------------------------- |
| 3.5 m | 30° |
| 4.5 m | 22.5° |
| 6 m | 17° |
| 8 m | 13° |
| 15 m | 8° |

**Outputs.**

- **Limiting axle.** The script adds 1% grip to one axle and reads the
  change in max lateral g.
- **Time.** The time for a 180° turn at radius R: arc time plus exit
  carry-over `Δv / a_x`, with a rear traction limit of `a_x` = 1.17 g.

Ackermann % uses the cotangent convention:
`100 · (cot δ_outer − cot δ_inner) · L / t`.

## Result

![Steering geometry](ackermann_curves.png)

**Geometry.**

- Front v19 is parallel steer. Its Ackermann is +1.4%, +1.2% and +0.7% at
  10, 20 and 30 mm of rack. Orion is −26%, −29% and −35%.
- Front v19 needs 30° at both wheels to hold R = 3.5 m at the limit. That
  is 42 mm of rack travel, or 171° of handwheel at Orion's 88.9 mm/rev.
- The SHK gives Front v19 −4.85° caster. The upper ball joint is 12.8 mm
  ahead of the lower one.

![Apex grip and trail-braking grip against Ackermann](grip_vs_ackermann.png)

**Apex.** Change in max lateral g compared with Front v19. Each cell shows
the nominal value, then the range over 12 cases (two tire stiffness
scales, both slip signs, three LLTDs).

| R | +50% | +75% | +100% | Time per 180° turn, +75% |
| - | ---- | ---- | ----- | ------------------------ |
| 3.5 m | +9.8% (+5.6 to +11.4) | +11.3% (+6.4 to +13.5) | +10.3% (+6.1 to +13.1) | −121 ms (−69 to −145) |
| 4.5 m | +3.9% (+1.7 to +4.9) | +4.2% (+1.9 to +5.6) | +3.7% (+1.5 to +5.5) | −52 ms (−23 to −69) |
| 6 m | +1.0% (−0.5 to +1.5) | +1.0% (−0.7 to +1.7) | +0.8% (−0.9 to +1.7) | −14 ms (+10 to −25) |
| 8 m | +0.2% (−0.2 to +0.5) | +0.2% (−0.3 to +0.6) | +0.1% (−0.4 to +0.6) | −3 ms (+6 to −10) |
| 15 m | 0.0% (0.0 to +0.1) | 0.0% (−0.1 to +0.2) | 0.0% (−0.1 to +0.2) | 0 ms (+1 to −4) |

- With 10% steps, the apex optimum is +80% at 3.5 m and +70% at 4.5 m.
- At 8 m and 15 m every setting from 0% to +100% is within 0.6%. Open
  corners do not choose the Ackermann.
- Negative Ackermann loses grip. At 3.5 m, −50% gives −11.8% to −14.3%.

**Balance.**

- At LLTD 52% and 62%, every case at 3.5 m and 4.5 m is front-limited.
  1% more front grip gives +0.7% to +0.9% max lateral g. 1% more rear
  grip gives nothing.
- At LLTD 32%, Front v19 is still front-limited at 3.5 m. +75% then makes
  the car rear-limited there, with +8.9%.
- LLTD sets the balance in open corners. Ackermann sets it in tight
  corners, where the front loses grip to toe mismatch, not to load
  transfer.

**Trail braking at 84% bias.** Change in max lateral g compared with
Front v19 at the same braking. Each cell shows the normalized-slip result,
then the ellipse result. Every case is front-limited.

| R, braking | +50% | +75% | +100% |
| ---------- | ---- | ---- | ----- |
| 3.5 m, 0.3 g | +7.3 / +7.2% | +8.0 / +8.0% | +6.2 / +6.5% |
| 3.5 m, 0.5 g | +8.3 / +7.6% | +8.2 / +8.0% | +4.1 / +5.4% |
| 4.5 m, 0.3 g | +3.0 / +3.0% | +3.1 / +3.7% | +2.2 / +2.5% |
| 4.5 m, 0.5 g | +2.9 / +3.0% | +1.9 / +2.7% | −0.6 / +1.4% |

- Braking moves the best setting down. At 0.5 g, +50% and +75% are within
  0.4% at 3.5 m, and +50% is best at 4.5 m. +100% falls behind, because
  the light inner front runs a large slip angle and a large brake force at
  the same time.
- The inner front uses at most 71% of its braking capacity at the limit.
  Lock does not set the limit at 0.5 g or less.
- At 70% hydraulic bias many cases become rear-limited, and more Ackermann
  does not help there. That is a brake-bias trade, not a steering result.

**Regen through the diff.** The diff's coast lock brakes the outer rear
harder than the inner rear. That is an understeer moment. It lowers the
Front v19 limit at 3.5 m and 0.5 g from 1.095 to 1.048 g. Every case stays
front-limited, also at 70% effective front share. At 0.5 g, +75% is best
at 3.5 m (+8.2%) and +50% is best at 4.5 m (+3.2%). +100% is worst of the
three in every case.

**Throttle.** Change in max lateral g compared with Front v19 at the same
drive, ellipse tire. Points where a rear wheel spins are left out.

| R, drive | Open diff, +50 / +75 / +100% | LSD, +50 / +75 / +100% |
| -------- | ---------------------------- | ---------------------- |
| 3.5 m, 0 g | +9.7 / +11.2 / +10.2% | +11.8 / +13.6 / +12.4% |
| 3.5 m, 0.2 g | +10.7 / +12.5 / +11.5% | wheelspin |
| 4.5 m, 0 g | +3.8 / +4.1 / +3.6% | +4.9 / +5.3 / +4.6% |
| 4.5 m, 0.2 g | +4.4 / +4.8 / +4.3% | +5.8 / +6.4 / +5.6% |

- +75% is best in every case without wheelspin.
- The LSD makes Ackermann worth more. Its locking torque goes to the slower
  inner rear wheel, which is an understeer moment. The car becomes more
  front-limited, and the front toe mismatch costs more.
- At 0.4 g drive with the LSD, the inner rear spins in every case.
- This model cannot show power-on rotation. The stable-branch rule and
  the wheelspin rule keep the rear below saturation, and rear slip stays at
  2° to 4°. Rotation moves the turn center forward. As a hand estimate,
  each extra 2.5° of rear slip lowers the Ackermann need by about 6 points
  at 3.5 m.

**Static toe.** Best Ackermann and its gain compared with Front v19 at 0°
toe, in 10% steps. Negative toe-out is toe-in.

| Total toe-out | 3.5 m best | 4.5 m best |
| ------------- | ---------- | ---------- |
| −1.0° (toe-in) | +90% (+11.3%) | +90% (+4.2%) |
| −0.5° | +80% (+11.3%) | +80% (+4.2%) |
| 0° | +80% (+11.3%) | +70% (+4.2%) |
| +0.5° | +70% (+11.3%) | +60% (+4.2%) |
| +1.0° | +70% (+11.2%) | +50% (+4.2%) |

- Toe replaces Ackermann. It does not add grip: the best gain is the same
  at every toe.
- 1° of toe-out replaces about 10 Ackermann points at 3.5 m and about 20
  at 4.5 m. Toe-in needs the same amount more.
- Toe-out has costs that are not in this model: straight-line scrub, tire
  heat and darting.

**Mass and CG.** Mass ±10 kg, CG height ±20 mm and front weight ±3 points
change the gains by less than 0.4 points. +75% stays best in every
variant.

**Screening result.** Pro-Ackermann in the +50% to +80% band at hairpin
steer (20° to 30° roadwheel), measured with 0° static toe.

- The apex, throttle and regen cases favor +75% to +80%.
- Hydraulic trail braking at 4.5 m favors +50%.
- +100% is worst of the band under braking.
- Front v19's parallel steer gives up 6% to 14% of lateral g at 3.5 m.
- Choose inside the band by packaging, and set the target together with
  the static toe.

**Drag.** At 80% of the limit and R = 3.5 m, the drive force to hold speed
is 258 to 306 N for Front v19, 125 to 198 N for +50% and 84 to 173 N for
+75%. At 8 m, Front v19 and +50% are within 6 N.

**How to get more Ackermann.** Moving the tie rod outer point 32 mm
outboard gives +50% (48.6%, 50.0% and 52.6% at 10, 20 and 30 mm of rack).
This has three costs:

- It puts the point 19 mm inboard of the wheel center plane. Check the
  packaging in CAD.
- It adds bump steer: −0.18° and +0.21° of toe at −25 mm and +25 mm of
  jounce. The rack pickup must move to remove it.
- The rack position and the rack length are the other levers. This study
  did not map them.

**Across events.**

- Acceleration: no effect.
- Skidpad (R = 9.125 m): between the 8 m and 15 m results, so no effect.
- Autocross and endurance: multiply the time per tight corner by the number
  of corners under about 6 m on the course. That takes one baseline lap,
  not a lap-sim sweep.

**Confidence.** The limits are in the scope and the inputs, not in the
math.

- **Math.** Solves converge to a residual under 1e-7. The gain falls about
  as 1/R⁴, as mismatch² predicts. An independent implementation matches
  the braking, throttle and toe results to 0.001 g. The Orion geometry
  matches independent numbers.
- **Scope.** The model is quasi-steady. It cannot show power-on rotation
  or trail-brake rotation beyond the steady state. It has no camber or
  steer camber, no compliance steer, no aligning moment and no yaw
  acceleration. Rotation lowers the Ackermann need, so treat the apex gain
  as an upper bound. The 180° times assume the whole turn is at radius R.
- **Inputs.** The tire is a raw TTC fit with an unvalidated 0.623 grip
  scale. The gain changes by up to 2× across the tire cases. The other
  carryovers are listed above.
- **What holds in every case:** pro-Ackermann beats Front v19 in tight
  corners, open corners do not care, +100% is worst of the band under
  braking, and toe trades one-for-one with Ackermann.

## Check before design freeze

These inputs are not confirmed for 2027. Items 1 to 3 change the toe
difference between the front wheels, which is the mechanism this study
measures.

1. **Geometry source.** The Front v19 SHK and the hardpoint tracker
   disagree. Decide which is the source of truth. Check the −4.85° caster.
2. **Static toe and camber.** No 2027 setting exists. Set the Ackermann
   target together with the static toe.
3. **Compliance steer.** Toe change under load is not modeled and not
   measured.
4. **Rack travel and clearance.** Front v19 needs 42 mm of rack for
   R = 3.5 m at the limit. The rack stops and the swept tire and body
   clearance are not measured. This study does not show that Front v19
   clears the hairpin.
5. **Brake bias and regen.** Get the 2027 hydraulic bias and the regen
   torque commanded under braking.
6. **Diff.** The diff-off-the-back architecture is decided. The clutch LSD
   and its tune are not. The throttle and regen results depend on them.
7. **Springs, bars, LLTD, mass and CG.** None are confirmed for 2027. Do
   not use the LLTD calculated from the tracker geometry for the SHK car.

Rerun the study when these are known.

## Provenance

Front v19 steering hardpoints on the Orion vehicle.

```json
{
  "study": "front-ackermann",
  "bobsim_sha": "4da577af1b04d86c53706eb6e80fb0064f71cee6",
  "vehicle_sha256": "3ab02bbf3e573aad0330bc37ce40fd254090d33dabd3760462c51da74e30afe8",
  "utc": "2026-09-26T17:01:53+00:00"
}
```
