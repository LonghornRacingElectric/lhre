# front-ackermann

## Question

How much Ackermann should the 2027 Front v19 steering have? How much does
it change the car in tight corners?

## Method

**Why not a lap-sim sweep.** The BobSim reduced model gives both front
wheels one steer angle. It builds the GGV at zero yaw rate, so both front
wheels see the same path angle. Its tire has no force peak. A lap-sim
sweep therefore cannot see Ackermann.

**Model.** `run.py` solves a steady-state, constant-radius corner with four
wheels:

- Each wheel sees its own path angle (yaw rate × wheel position).
- Each tire uses the MF52 lateral force from the `.tir` file, through
  BobSim `_5_App/tire_eval.py`. The fit has a force peak, so a slip-angle
  mismatch between the two front tires costs grip.
- Lateral load transfer is `m·ay·h`, split front to rear by LLTD.
- The script solves force balance and yaw balance for body slip and steer.
  A bisection on lateral g finds the limit.
- It keeps only stable states: each axle's lateral force must still rise
  with slip. This removes a drift state with 16° to 22° of rear slip.

**Geometry.** The Front v19 front-left hardpoints come from
`2027_FrontV19.shk`, `FRONT SUSPENSION` block (SHA-256 `361e76f3…e467ae02`).
Positive x points forward, and the origin is on the front axle centerline.
The rear SHK puts its wheel center at x = −1549.4 mm, which confirms the
frame. BobSim `kin_py` solves inner and outer roadwheel angle against rack
travel. `run.py` holds these points as constants and writes the changed
vehicle copy to `out/front-ackermann/vehicle_front_v19.yml`.

**Other inputs.** These are Orion carryovers from `vehicle/vehicle.yml`,
because the 2027 values are not known:

| Input | Value |
| ----- | ----- |
| Mass with driver | 261.1 kg |
| CG height | 0.280 m |
| Front static weight | 48.3% |
| LLTD, front | 52.0%, from BobSim nominal roll stiffness |
| Tire | `16x7p5_10_12psi`, LMUY = LMUX = 0.623 |
| Wheelbase, front track | 1.549 m, 1.245 m (Front v19) |

**Sweep.** The script compares the Front v19 curve with constant Ackermann
from −50% to +100% in 25% steps. It runs at R = 3.5, 4.5, 6 and 8 m (CG path
radius). A 9 m outside-diameter hairpin puts the CG path near 3.5 to 4 m.

| R | Front v19 roadwheel angle at the limit |
| - | -------------------------------------- |
| 3.5 m | 31° |
| 4.5 m | 23° |
| 6 m | 17° to 18° |
| 8 m | 13° to 14° |

**Cases.** There are 12 cases for each point:

- tire stiffness scale LKY = 1 or 0.623;
- both tire slip signs, which cover left and right turns;
- LLTD front 32%, 52% or 62%. At 32% the car is rear-limited at 8 m, so
  this case stands in for a balanced car.

The nominal case is LKY = 1, positive slip, and LLTD 52%.

**Outputs.**

- **Limiting axle.** The script adds 1% grip to one axle and reads the
  change in max lateral g.
- **Drag.** It reports the drive force needed to hold speed at 80% of the
  Front v19 limit.
- **Time.** It reports the time for a 180° turn at radius R: arc time plus
  exit carry-over `Δv / a_x`. It uses a rear traction limit of
  `a_x` = 1.17 g.

Ackermann % uses the cotangent convention:
`100 · (cot δ_outer − cot δ_inner) · L / t`.

## Result

![Steering geometry](ackermann_curves.png)

**Geometry.**

- Front v19 is parallel steer. Its Ackermann is +1.4%, +1.2% and +0.7% at
  10, 20 and 30 mm of rack. Orion is −26%, −29% and −35%.
- Front v19 steers slowly. To hold R = 3.5 m at the limit, it needs 30.6°
  at both wheels. That is 43 mm of rack travel, or 174° of handwheel at
  Orion's 88.9 mm/rev. Compare this with the real rack stops.
- The SHK gives Front v19 **−4.85° caster**. The upper ball joint is
  12.8 mm ahead of the lower one. Check this against CAD. With negative
  caster, the outer front wheel gains positive camber when steered. This
  study does not model camber.

![Grip and drag against Ackermann](grip_vs_ackermann.png)

**Grip.** The table gives the change in max lateral g compared with
Front v19. Each cell shows the nominal value, then the range over the
12 cases.

| R | +50% | +75% | +100% | Time per 180° turn, +75% |
| - | ---- | ---- | ----- | ------------------------ |
| 3.5 m | +10.2% (+7.1 to +11.4) | +11.8% (+7.6 to +13.5) | +10.7% (+6.7 to +13.2) | −128 ms (−86 to −148) |
| 4.5 m | +4.4% (+2.3 to +5.2) | +4.7% (+2.3 to +6.1) | +4.1% (+1.9 to +6.1) | −59 ms (−30 to −76) |
| 6 m | +1.2% (−0.8 to +2.2) | +1.2% (−0.9 to +2.0) | +1.0% (−1.0 to +2.0) | −18 ms (+13 to −29) |
| 8 m | +0.3% (−0.2 to +0.6) | +0.3% (−0.3 to +0.7) | +0.2% (−0.4 to +0.8) | −5 ms (+6 to −12) |

- +75% gives the most grip at 3.5 m in 12 of 12 cases, and at 4.5 m in 10
  of 12. +100% wins the other 2. +50% to +100% is a plateau.
- Negative Ackermann loses grip. At 4.5 m, −50% gives −6.4% to −9.0%. At
  3.5 m, −50% gives −11.7% to −14.2%, and it has no stable state in 3 of
  12 cases.
- The Front v19 limit is 1.02 to 1.17 g at 3.5 m and 1.25 to 1.37 g at 8 m.

**Balance.** At 3.5 m and 4.5 m, Front v19 is front-limited in all 12
cases. 1% more front grip gives +0.7% to +1.3% max lateral g. 1% more rear
grip gives nothing. A more rearward LLTD does not change this:

- With LLTD 32% the car is rear-limited at 8 m. At 3.5 m it is still
  front-limited: Front v19 reaches 1.15 g, and +75% adds 11.6%.
- LLTD sets the balance in open corners. Ackermann sets it in tight
  corners, where the front loses grip to toe mismatch, not to load
  transfer.
- Where the car is rear-limited (8 m at LLTD 32%), +75% costs 0.1% to 0.3%.

**Drag.** At 80% of the limit and R = 3.5 m, the drive force to hold speed
is 242 to 283 N for Front v19 and 74 to 147 N for +75%. At 8 m the two are
within 7 N.

**How to get more Ackermann.** Moving the tie rod outer point 32 mm
outboard gives +50% (48.6%, 50.0% and 52.6% at 10, 20 and 30 mm of rack).
This has three costs:

- It puts the point 19 mm inboard of the wheel center plane. Check the
  packaging in CAD.
- It adds bump steer: −0.18° and +0.21° of toe at −25 mm and +25 mm of
  jounce. The rack pickup must move to remove it.
- It does not reach +100% within 60 mm of travel. The rack position and
  the rack length are the other levers.

**Across events.**

- Acceleration: no effect.
- Skidpad (R = 9.125 m): less than the 8 m result, so a few ms at most.
- Autocross and endurance: multiply the time per tight corner by the number
  of corners under about 6 m on the course. That takes one baseline lap,
  not a lap-sim sweep.

**Confidence.**

- High that +50% to +100% beats Front v19 in tight corners. The ranking
  holds in all 12 cases at 3.5 m.
- Moderate to low for the size of the gain. It changes by up to 2× across
  the tire cases. The tire is a raw TTC fit, and the 0.623 grip scale is
  not validated.
- The model is a steady state at neutral throttle, and the rear only runs
  2° to 3° of slip. A driver who rotates the car with throttle or trail
  braking moves the turn center forward. That lowers the Ackermann need.
  Treat the gain as an upper bound.
- The 180° times assume the whole turn is at radius R. Real hairpins spend
  part of the turn at a larger radius.
- The model leaves out camber (all 0°), steer camber, compliance steer,
  tire aligning moment, aero and rear combined slip.

## Provenance

Front v19 steering hardpoints on the Orion vehicle.

```json
{
  "study": "front-ackermann",
  "bobsim_sha": "4da577af1b04d86c53706eb6e80fb0064f71cee6",
  "vehicle_sha256": "3ab02bbf3e573aad0330bc37ce40fd254090d33dabd3760462c51da74e30afe8",
  "utc": "2026-09-25T05:15:06+00:00"
}
```
