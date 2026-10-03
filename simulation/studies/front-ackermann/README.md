# front-ackermann

## Question

Can the 2027 Front v20 steering reach the tightest hairpin? How much
Ackermann should it have, given that packaging allows at most
+<!-- out:readme.json#pack_pct -->42<!-- /out -->%?

This is a screening study. It gives the direction and the sensitivities,
not final numbers. See [Check before design freeze](#check-before-design-freeze).

## Answer

- **Fix the steering lock first.** With 1.25 in of rack, Front v20 steers
  <!-- out:readme.json#steer_at_travel_deg|.1f -->22.4<!-- /out -->°. A
  minimum hairpin at the grip limit needs
  <!-- out:readme.json#steer_needed_3p5_deg|.1f -->29.7<!-- /out -->°. This
  costs more than any Ackermann choice.
- **Use the packaging limit, +<!-- out:readme.json#pack_pct -->42<!-- /out -->%.**
  At the 3.5 m hairpin apex it changes lateral g by
  <!-- out:readme.json#pack_apex_3p5|+.1f -->+10.1<!-- /out -->% compared
  with Front v20, which is
  <!-- out:readme.json#pack_share_of_best_3p5|.0f -->80<!-- /out -->% of the
  best apex gain. It saves <!-- out:readme.json#pack_lap_s -->0.27 to 0.53 s<!-- /out -->
  per Michigan lap.
- **More Ackermann would not help much.** The best per-lap setting is
  <!-- out:readme.json#best_lap_s -->+50%<!-- /out -->, and the lap times
  near it are almost equal. Under trail braking, every pro-Ackermann
  setting loses grip, and the loss grows with Ackermann.
- **Front v20 is slightly anti-Ackermann**
  (<!-- out:readme.json#v20_ackermann_pct -->−6.3% to −7.8%<!-- /out -->). Any
  pro-Ackermann linkage is better in tight corners.
- **The tire grip scale moves the size of the gain the most.** See
  [Sensitivity](#sensitivity). The direction stays the same.

## Result

`make study` fills in the numbers between `out:` markers from the last
run. "Change" means change in max lateral g compared with Front v20.

### Steering lock and linkage

![Steering geometry](ackermann_curves.png)

- The tightest CG radius at the grip limit is
  <!-- out:readme.json#tightest_radius_m|.1f -->4.3<!-- /out --> m. The
  tightest legal CG path is near 3.5 m.
- The tie rod outer point is
  <!-- out:readme.json#arm_offset_mm|.0f -->82<!-- /out --> mm from the
  kingpin axis. With this arm, R = 3.5 m needs
  <!-- out:readme.json#rack_needed_mm|.1f -->41.2<!-- /out --> mm of rack.

Each option below reaches its own hairpin steer at 90% of rack travel and
keeps bump steer at zero at center. With a front rack, more Ackermann
moves the tie rod point toward the wheel.

<!-- out:linkage.md -->

| Option | Tie rod outer move | To wheel center plane | Arm to kingpin | Rack pickup move | Toggle margin at inner lock | Bump toe at inner lock, ±25 mm |
| ------ | ------------------ | --------------------- | -------------- | ---------------- | --------------------------- | ------------------------------ |
| Front v20 | – | 51 mm | 82 mm | – | 70° | +0.2° / −0.3° |
| 0% | 24 mm rearward, 9 mm outboard | 42 mm | 58 mm | 5 mm outboard, 1 mm down | 58° | +0.4° / −0.5° |
| +42% | 18 mm rearward, 28 mm outboard | 23 mm | 68 mm | 12 mm outboard, 2 mm down | 44° | +0.5° / −0.6° |
| +50% | 16 mm rearward, 33 mm outboard | 18 mm | 71 mm | 14 mm outboard, 2 mm down | 41° | +0.5° / −0.6° |
| +70% | 13 mm rearward, 46 mm outboard | 6 mm | 80 mm | 19 mm outboard, 2 mm down | 34° | +0.5° / −0.7° |

<!-- /out -->

### Apex

![Apex grip and trail-braking grip against Ackermann](grip_vs_ackermann.png)

Nominal value, then the range over 12 cases (two tire stiffness scales,
both slip signs, three LLTDs).

<!-- out:apex.md -->

| R | +42% | +50% | +75% | +100% | Time per 180° turn, +42% |
| - | ---- | ---- | ---- | ----- | ------------------------ |
| 3.5 m | +10.1% (+5.8 to +11.9) | +11.0% (+5.7 to +13.2) | +12.6% (+5.1 to +15.5) | +12.6% (+4.1 to +15.4) | −107 ms |
| 4.5 m | +3.9% (−0.6 to +5.5) | +4.1% (−0.7 to +5.9) | +4.5% (−1.1 to +6.7) | +4.4% (−1.6 to +6.7) | −47 ms |
| 6 m | +0.7% (−0.4 to +1.6) | +0.7% (−0.5 to +1.8) | +0.8% (−0.7 to +2.0) | +0.4% (−0.9 to +2.1) | −10 ms |
| 8 m | 0.0% (−0.2 to +0.5) | −0.1% (−0.2 to +0.6) | −0.2% (−0.3 to +0.7) | −0.3% (−0.4 to +0.7) | +1 ms |
| 15 m | 0.0% (0.0 to +0.1) | 0.0% (−0.1 to +0.1) | 0.0% (−0.1 to +0.2) | −0.2% (−0.2 to +0.2) | 0 ms |

<!-- /out -->

- Ackermann matters only in tight corners. Above 6 m, the change is under
  1%.
- Once the rear axle limits, more Ackermann stops paying.

### Braking

`vehicle.yml` has 84% front brake bias, with no source. The study uses
65%. The straight-line limit is highest at
<!-- out:readme.json#ideal_bias_pct|.0f -->68<!-- /out -->% front.

<!-- out:bias.md -->

| Front bias | Locks first | Straight-line limit |
| ---------- | ----------- | ------------------- |
| 65% | rear | 1.46 g |
| 84% | front | 1.18 g |

<!-- /out -->

Trail braking at 65%, normalized-slip / ellipse tire:

<!-- out:braking.md -->

| R, braking | +25% | +42% | +75% | +100% |
| ---------- | ---- | ---- | ---- | ----- |
| 3.5 m, 0.3 g | −0.4 / −0.3% | −0.8 / −0.7% | −2.1 / −2.0% | −3.5 / −3.4% |
| 3.5 m, 0.5 g | −0.9 / −0.6% | −1.5 / −1.1% | −3.1 / −2.7% | −4.7 / −4.2% |
| 4.5 m, 0.3 g | −0.7 / −0.7% | −1.1 / −1.1% | −2.1 / −2.0% | −2.8 / −2.7% |
| 4.5 m, 0.5 g | −0.9 / −0.9% | −1.5 / −1.4% | −2.6 / −2.5% | −3.5 / −3.3% |

<!-- /out -->

Braking moves load to the front, so the rear limits. More Ackermann then
costs grip.

### Throttle and regen

Throttle, each cell +50% / +75% / +100%. "Spin" means a rear wheel sets
the limit.

<!-- out:throttle.md -->

| R, drive | Open diff | Orion LSD | Planned LSD |
| -------- | --------- | --------- | ----------- |
| 3.5 m, 0 g | +9.9 / +10.0 / +9.4% | +13.2 / +15.2 / +15.0% | +13.4 / +15.4 / +15.2% |
| 4.5 m, 0 g | +1.6 / +1.4 / +1.1% | +5.3 / +5.9 / +5.7% | +5.2 / +5.7 / +5.5% |
| 3.5 m, 0.2 g | +11.7 / +12.6 / +12.6% | spin | spin |
| 4.5 m, 0.2 g | +3.5 / +3.6 / +3.5% | spin | spin |

<!-- /out -->

Regen through the diff, each cell the +50% change, the best Ackermann and
the axle that limits Front v20:

<!-- out:regen.md -->

| Diff, front share, braking | 3.5 m | 4.5 m |
| -------------------------- | ----- | ----- |
| orion, 65%, 0.3 g | +6.9%, best +25%, front-limited | −0.9%, best +0%, rear-limited |
| orion, 65%, 0.5 g | +7.8%, best +25%, front-limited | −1.2%, best +0%, rear-limited |
| orion, 55%, 0.3 g | +5.8%, best +25%, front-limited | −1.0%, best +0%, rear-limited |
| orion, 55%, 0.5 g | +2.4%, best +0%, front-limited | −1.5%, best +0%, rear-limited |
| planned, 65%, 0.3 g | +1.4%, best +0%, front-limited | −1.1%, best +0%, rear-limited |
| planned, 65%, 0.5 g | +3.2%, best +0%, front-limited | −1.3%, best +0%, rear-limited |
| planned, 55%, 0.3 g | −0.5%, best +0%, front-limited | −1.1%, best +0%, rear-limited |
| planned, 55%, 0.5 g | −1.1%, best +0%, rear-limited | −1.5%, best +0%, rear-limited |

<!-- /out -->

An LSD that locks hard on drive makes Ackermann worth more on exit. A
high coast lock or more regen on the rear makes it worth less.

### Toe

Best Ackermann at each static toe:

<!-- out:toe.md -->

| Total toe-out | 3.5 m best | 4.5 m best |
| ------------- | ---------- | ---------- |
| −1.0° (toe-in) | +100% | +100% |
| −0.5° | +90% | +90% |
| 0.0° | +90% | +80% |
| +0.5° | +80% | +70% |
| +1.0° | +70% | +60% |

<!-- /out -->

1° of toe-out replaces about
<!-- out:readme.json#points_per_deg_toe_3p5|.0f -->10<!-- /out --> Ackermann
points at 3.5 m. Toe does not add grip, and it costs straight-line scrub.

### Lap time

Michigan 2019 endurance, minimum-curvature line. The range comes from the
exit and entry acceleration assumed after each corner.

<!-- out:lap.md -->

| Linkage | Time saved per lap vs Front v20 |
| ------- | ------------------------------- |
| +25% | 0.23 to 0.48 s |
| +42% | 0.27 to 0.53 s |
| +50% | 0.27 to 0.54 s |
| +75% | 0.27 to 0.52 s |
| +100% | 0.20 to 0.36 s |

<!-- /out -->

### Sensitivity

- **Mass, CG, weight split and camber** change the gains by at most
  <!-- out:readme.json#mass_gap_pts|.1f -->0.4<!-- /out --> points.
- **Tire grip scale.** The 0.623 scale is not validated. A variant at 0.75
  (`ARGS="--mu 0.75" V=mu075`, run 2026-10-03) raises the grip by about
  17%. The car then becomes rear-limited in more corners, so Ackermann
  helps less. +42% then saves 0.04 to 0.06 s per lap, and +75% loses up to
  0.07 s. +42% stays within 0.02 s of the best setting at both scales.

### Confidence

- **Math.** The solves converge to a residual under 1e-7. A closed-form
  optimum matches the solver where the front limits.
- **Scope.** The model is quasi-steady. It has no transient yaw, steer
  camber, compliance steer or aligning moment.
- **Inputs.** The tire grip scale, the balanced-car LLTD, the 65% bias and
  the diff values are assumptions.

## Method

The BobSim lap sim gives both front wheels one steer angle, so it cannot
see Ackermann. `run.py` instead solves a quasi-steady corner at constant
radius with four wheels:

- Each wheel has its own path angle and an MF52 tire from BobSim
  `_5_App/tire_eval.py`.
- Load transfer uses the body-frame acceleration. LLTD splits the lateral
  transfer.
- The solve balances forces and yaw moment, and keeps only stable states.
- Braking, regen and throttle add a longitudinal force. The LSD uses the
  BobLib `Differential1D` law.
- BobSim `kin_py` gives the steering curve from the Front v20 hardpoints.
  A least-squares solve moves the tie rod outer point and the rack pickup
  for each linkage option.
- The lap estimate uses BobSim's minimum-curvature line for Michigan 2019.

Ackermann % is `100 · (cot δ_outer − cot δ_inner) · L / t`.

**Inputs.**

| Input | Value | Source |
| ----- | ----- | ------ |
| Mass with driver | 263.1 kg (430 + 150 lb) | team 2027 estimate |
| CG height | 0.279 m (11 in) | team 2027 estimate |
| Front static weight | 45% | team 2027 estimate |
| LLTD, front | <!-- out:readme.json#lltd_front_pct|.1f -->34.2<!-- /out -->% | max lateral g at 15 m |
| Rack travel | 31.75 mm (1.25 in) each way | team |
| Ackermann limit | +<!-- out:readme.json#pack_pct -->42<!-- /out -->% | suspension lead, packaging |
| Brake bias, front | 65% | first principles |
| Tire | `16x7p5_10_12psi`, grip scale 0.623 | Orion carryover |
| Front hardpoints | Front v20 | team sheet, 2026-09-24 |

The v20 sheet gives x without a sign. The signs follow Front v19, which has
the same magnitudes. The sheet has no wheel center, so the study keeps the
v19 wheel center.

**Variants.** `run.py` takes `--mu`, `--ackermann` and `--bias`. For
example:

```bash
make study S=front-ackermann ARGS="--mu 0.75" V=mu075
```

## Check before design freeze

1. **Geometry.** Confirm the v20 x signs, the wheel center and the −4.85°
   caster.
2. **Steering lock.** Pick more rack travel or a shorter steering arm.
   Check clearance and the inner-wheel toggle margin at full lock.
3. **Linkage packaging.** Check the tie rod outer point against the wheel
   and brake in CAD. Move the rack pickup to keep bump steer at zero.
4. **Tire grip scale.** Validate it on track. It moves the gains the most.
5. **Brake bias, regen and diff.** Get the 2027 hydraulic bias, the regen
   torque and the Drexler ramps and preload.
6. **Toe and compliance.** Set static toe with the Ackermann. Measure toe
   change under load.

## Provenance

Front v20 steering hardpoints, team 2027 mass and CG, balanced LLTD, Orion
rear and tire.

<!-- out:provenance.json -->

```json
{
  "study": "front-ackermann",
  "bobsim_sha": "4da577af1b04d86c53706eb6e80fb0064f71cee6",
  "vehicle_sha256": "3ab02bbf3e573aad0330bc37ce40fd254090d33dabd3760462c51da74e30afe8",
  "utc": "2026-10-03T21:04:58+00:00"
}
```

<!-- /out -->
