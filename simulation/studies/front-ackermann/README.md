# front-ackermann

## Question

Is pro-Ackermann or anti-Ackermann steering better for our car, and by how much? A separate section checks what the
Front V33 hardpoints can package. This is a screening study. It gives the direction and the sensitivities, not final
numbers.

## Answer

- **Pro is better than anti for our car.** On the endurance lap, every anti law loses time and every pro law gains
  time, at both grip scales. Anti costs more than pro gains. The gain flattens above +50%, so Front V33 gets most of
  it.
- **Best law on the endurance lap.** At grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out -->, it is
  <!-- out:readme.json#best_lap_law -->+75%<!-- /out -->. It gains
  <!-- out:readme.json#best_lap_gain_s|.2f -->0.09<!-- /out --> s per lap over parallel steer. At grip scale
  <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out -->, it is
  <!-- out:readme.json#best_lap_law_sensitivity -->+75%<!-- /out --> and gains
  <!-- out:readme.json#best_lap_gain_sensitivity_s|.2f -->0.08<!-- /out --> s. A positive gain is a faster lap.
- **Pro against anti on the lap.** The +50% law changes the endurance lap by
  <!-- out:readme.json#pro50_lap_s|+.2f -->−0.08<!-- /out --> s, and the −50% law by
  <!-- out:readme.json#anti50_lap_s|+.2f -->+0.27<!-- /out --> s. At the higher grip scale they change it by
  <!-- out:readme.json#pro50_lap_sensitivity_s|+.2f -->−0.07<!-- /out --> s and
  <!-- out:readme.json#anti50_lap_sensitivity_s|+.2f -->+0.16<!-- /out --> s. Negative means faster.
- **Operating map.** Of <!-- out:readme.json#map_cells -->30<!-- /out --> operating points, a pro law wins at
  <!-- out:readme.json#pro_cells -->8<!-- /out -->, an anti law at <!-- out:readme.json#anti_cells -->11<!-- /out --> and
  parallel at <!-- out:readme.json#parallel_cells -->11<!-- /out -->. At a 3.5 m hairpin in steady state, the best law is
  <!-- out:readme.json#best_3p5_steady -->+75%<!-- /out --> with
  <!-- out:readme.json#best_3p5_steady_gain_pct|+.1f -->+12.7<!-- /out -->% grip. Under the hardest braking at 3.5 m, the
  best law is <!-- out:readme.json#best_3p5_hard_brake -->-25%<!-- /out -->.
- **Front V33.** It has <!-- out:readme.json#base_ackermann_pct -->+42.5% to +45.9%<!-- /out --> Ackermann, at the
  +<!-- out:readme.json#pack_pct -->42<!-- /out -->% packaging limit. It changes the endurance lap by
  <!-- out:readme.json#design_lap_s|+.2f -->−0.07<!-- /out --> s, and by
  <!-- out:readme.json#design_lap_sensitivity_s|+.2f -->−0.07<!-- /out --> s at the higher grip scale.
- **Steering lock is a separate gate.** The comparison gives every option unlimited lock. At full rack travel, Front V33
  has <!-- out:readme.json#steer_at_travel_deg|.1f -->23.3<!-- /out -->° of mean roadwheel steer. A 3.5 m hairpin at the
  grip limit needs <!-- out:readme.json#steer_needed_3p5_deg|.1f -->25.5<!-- /out -->°.

**Why not copy other cars?** Many cars run anti-Ackermann or parallel steer. This paragraph is reasoning, not model
output. Large-radius cars barely see Ackermann: at 50 m, the front path-angle difference is about 0.1°. A front rack
makes pro-Ackermann hard to package. A video cannot resolve a few degrees of toe. The main physical argument for anti is
tire load sensitivity. The loaded outer tire wants more slip angle than the light inner tire. Our Magic Formula 5.2
(MF52) tire model includes load sensitivity. So the question is quantitative for our car. The answer holds for this car
and tire, not in general.

## Result

All results come from a screening model. Ackermann % is `100 · (cot δ_outer − cot δ_inner) · L / t`, with roadwheel
steer δ, wheelbase L and front track t. Positive is pro, negative is anti and zero is parallel steer. A grip change is
the change in max lateral g compared with parallel steer. A lap change is the change in lap time compared with parallel
steer. Negative means faster. The options are constant laws from −50% to +100% in 25-point steps. A constant law keeps the
same Ackermann at every steer angle. All laws use the same car, tires and track, and no hardpoints. Parallel steer, the
0% law, is the reference. Front V33 shows where the current design sits. The map steady row, laps, skidpad, hairpin
times and yaw derivatives use powered steady state. There, drive force through the planned limited-slip differential
(LSD) balances tire drag.

### Operating map

![Left: coasting apex grip change by Ackermann and corner radius, diamond = Front V33. Right: best constant law at each corner radius and braking or drive level, red = anti, blue = pro](grip_vs_ackermann.png)

The right panel and the table give the best law at each operating point, at grip scale
<!-- out:readme.json#mu_nominal -->0.623<!-- /out -->. An operating point is one corner radius and one braking or drive
level. Each cell shows the best law and its grip gain over parallel steer. "parallel" means that no law beats parallel
steer by more than 0.1%. F or R names the axle that limits the parallel car.

<!-- out:map.md -->

| Longitudinal | 3.5 m | 4.5 m | 6 m | 8 m | 11 m | 15 m |
| ------------ | ----- | ----- | --- | --- | ---- | ---- |
| 0.9 g braking | -25% (+1.7%) R | -50% (+2.1%) R | -50% (+1.0%) R | -50% (+0.3%) R | parallel R | parallel R |
| 0.6 g braking | -25% (+0.5%) R | -50% (+1.2%) R | -50% (+0.8%) R | -50% (+0.3%) R | parallel R | parallel R |
| 0.3 g braking | parallel R | -50% (+0.8%) R | -50% (+0.6%) R | -50% (+0.2%) R | parallel R | parallel R |
| steady | +75% (+12.7%) F | +75% (+4.8%) F | +75% (+1.2%) F | +75% (+0.3%) F | parallel F | parallel F |
| 0.15 g drive | +100% (+14.2%) F | +100% (+5.5%) F | +75% (+1.5%) F | +75% (+0.4%) F | parallel F | parallel F |

<!-- /out -->

At 3.5 m in powered steady state, the +50% law changes grip by
<!-- out:readme.json#pro50_apex_3p5|+.1f -->+10.7<!-- /out -->%. The −50% law changes it by
<!-- out:readme.json#anti50_apex_3p5|+.1f -->−15.0<!-- /out -->%. Two effects compete. Where the front axle limits, the
inner front wheel follows a tighter path, so pro-Ackermann helps. Load sensitivity favors anti. Where the rear axle
limits, front Ackermann cannot add grip. <!-- out:readme.json#map_failed_cells -->0<!-- /out --> map solves found no
limit, over all options and both grip scales.

### Continuous track

BobSim's quasi-steady-state (QSS) lap solver runs each option on the Michigan 2019 endurance minimum-curvature line. The
line is <!-- out:readme.json#track_length_m|.0f -->1989<!-- /out --> m long. It has
<!-- out:readme.json#corners_under_15m -->42<!-- /out --> corners tighter than 15 m. The tightest corner radius is
<!-- out:readme.json#tightest_corner_m|.1f -->4.5<!-- /out --> m. Each option gets its own acceleration envelope from the
four-tire model, with the 32 kW endurance power limit. The skidpad lap is one steady lap at R = 9.125 m.

<!-- out:lap.md -->

| Option | Endurance lap, grip 0.623 | Endurance lap, grip 0.75 | Skidpad lap, grip 0.623 | Skidpad lap, grip 0.75 |
| ------ | ------------------------- | ------------------------ | ----------------------- | ---------------------- |
| -50% | +0.27 s | +0.16 s | +0.013 s | +0.003 s |
| -25% | +0.12 s | +0.07 s | +0.005 s | +0.001 s |
| +25% | −0.06 s | −0.04 s | −0.003 s | −0.001 s |
| +50% | −0.08 s | −0.07 s | −0.005 s | −0.001 s |
| +75% | −0.09 s | −0.08 s | −0.005 s | −0.001 s |
| +100% | −0.07 s | −0.08 s | −0.004 s | −0.001 s |
| Front V33 | −0.07 s | −0.07 s | −0.004 s | −0.001 s |

<!-- /out -->

The next table splits the endurance lap change at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> by phase
and by corner radius. Exits include the straights. Pro gains on exits and in steady corners. It loses a little in
braking zones, where the rear axle limits. The last table gives the time change per 180° hairpin at the powered steady
limit.

<!-- out:where.md -->

| Option | Braking zones | Steady corners | Exits | Radius under 6 m (2% of lap) | Radius 6 to 15 m (23% of lap) | Radius over 15 m (75% of lap) |
| ------ | ------------- | -------------- | ----- | ---------------------------- | ----------------------------- | ----------------------------- |
| -50% | +0.05 s | +0.07 s | +0.15 s | +0.08 s | +0.14 s | +0.05 s |
| -25% | +0.02 s | +0.03 s | +0.07 s | +0.04 s | +0.06 s | +0.02 s |
| +25% | 0.00 s | −0.02 s | −0.04 s | −0.02 s | −0.03 s | −0.01 s |
| +50% | +0.02 s | −0.03 s | −0.07 s | −0.04 s | −0.03 s | −0.01 s |
| +75% | +0.03 s | −0.03 s | −0.08 s | −0.04 s | −0.03 s | −0.01 s |
| +100% | +0.04 s | −0.03 s | −0.07 s | −0.04 s | −0.02 s | −0.01 s |
| Front V33 | +0.02 s | −0.03 s | −0.06 s | −0.03 s | −0.03 s | −0.01 s |

<!-- /out -->
<!-- out:hairpin.md -->

| Option | 3.5 m | 4.5 m |
| ------ | ----- | ----- |
| -50% | +149 ms | +88 ms |
| -25% | +67 ms | +38 ms |
| +25% | −52 ms | −26 ms |
| +50% | −87 ms | −40 ms |
| +75% | −102 ms | −45 ms |
| +100% | −101 ms | −43 ms |
| Front V33 | −85 ms | −38 ms |

<!-- /out -->

### Limit behavior

<!-- out:limit.md -->

| R | -50% | +0% | +50% | Front V33 |
| - | ---- | --- | ---- | --------- |
| 3.5 m | +43.8 / 30.8 | +21.4 / 40.6 | +2.2 / 50.3 | +2.9 / 50.8 |
| 4.5 m | +23.9 / 33.2 | +8.1 / 41.5 | −2.5 / 48.4 | −1.8 / 48.4 |
| 8 m | −0.3 / 42.1 | −2.5 / 45.3 | −3.0 / 45.5 | −3.0 / 45.6 |

<!-- /out -->

Each cell gives stability / steering. Both are yaw-moment derivatives ×1000 per roadwheel degree, divided by weight
times wheelbase. Stability is the yaw moment per degree of sideslip. Positive is restoring. Steering is the yaw moment
per degree of mean roadwheel steer: the steering authority. Each option runs at 90% of its own limit lateral g. The
derivatives exclude yaw damping and tire lag. At 3.5 m, the +50% law has
<!-- out:readme.json#pro50_authority_ratio_3p5|.1f -->1.2<!-- /out --> times the steering authority of parallel steer.
Inference: this may give a quicker response near the limit. Pro lowers the static stability near the limit, and anti
raises it. At 4.5 m the +50% law and Front V33 go slightly negative. Yaw damping, which is not in the model, also
restores.

### Apex sweep

The apex sweep coasts, with no longitudinal force balance. The left panel and the table give its grip change by law and
corner radius. Each cell gives the nominal value, then the range over 12 cases. The cases cover two tire
cornering-stiffness scales, both slip signs and three lateral load transfer distributions (LLTD).

<!-- out:apex.md -->

| R | -50% | -25% | +25% | +50% | +75% | +100% |
| - | ---- | ---- | ---- | ---- | ---- | ----- |
| 3.5 m | −12.5% (−13.8 to −11.6) | −6.0% (−6.4 to −5.0) | +5.2% (+3.2 to +5.9) | +8.8% (+4.0 to +10.6) | +10.5% (+3.5 to +13.0) | +10.4% (+2.5 to +13.0) |
| 4.5 m | −7.3% (−8.3 to −3.8) | −3.3% (−3.9 to −0.4) | +2.2% (−0.3 to +3.0) | +3.4% (−0.6 to +4.9) | +3.8% (−1.1 to +5.7) | +3.7% (−1.6 to +5.7) |
| 6 m | −2.2% (−3.4 to +0.4) | −0.8% (−1.4 to +0.2) | +0.4% (−0.2 to +1.0) | +0.6% (−0.5 to +1.5) | +0.5% (−0.7 to +1.8) | +0.3% (−0.9 to +1.9) |
| 8 m | −0.3% (−1.0 to +0.2) | −0.1% (−0.4 to +0.1) | +0.1% (−0.1 to +0.3) | −0.1% (−0.2 to +0.5) | −0.2% (−0.3 to +0.6) | −0.2% (−0.4 to +0.7) |
| 15 m | 0.0% (−0.1 to +0.1) | 0.0% (−0.1 to 0.0) | −0.1% (−0.1 to +0.1) | −0.1% (−0.1 to +0.1) | −0.1% (−0.1 to +0.2) | −0.1% (−0.1 to +0.2) |

<!-- /out -->

The next table checks the solver against a closed-form optimum for a front-limited car.

<!-- out:optimum.md -->

| R | Front-limited optimum | Solver optimum (10% steps) | +75% limited by |
| - | --------------------- | -------------------------- | --------------- |
| 3.5 m | 87% | +90% | front |
| 4.5 m | 86% | +80% | rear |

<!-- /out -->

### Brake bias and toe

`vehicle.yml` sets a front bias with no source. The study uses 65% front. The best straight-line bias is
<!-- out:readme.json#ideal_bias_pct|.0f -->68<!-- /out -->% front, with a
<!-- out:readme.json#ideal_limit_g|.2f -->1.54<!-- /out --> g limit.

<!-- out:bias.md -->

| Front bias | Locks first | Straight-line limit |
| ---------- | ----------- | ------------------- |
| 65% | rear | 1.46 g |
| 84% | front | 1.18 g |

<!-- /out -->

The toe table gives the best law at each total static toe-out. One degree of toe-out replaces about
<!-- out:readme.json#points_per_deg_toe_3p5|.0f -->10<!-- /out --> points of Ackermann at 3.5 m. Toe-out also adds scrub
on the straights.

<!-- out:toe.md -->

| Total toe-out | 3.5 m best | 4.5 m best |
| ------------- | ---------- | ---------- |
| −1.0° (toe-in) | +100% | +100% |
| −0.5° | +90% | +90% |
| 0.0° | +90% | +80% |
| +0.5° | +80% | +70% |
| +1.0° | +70% | +60% |

<!-- /out -->

### Sensitivity and confidence

- **Secondary inputs.** Mass, center of gravity (CG) height, weight split and camber move the grip change by at most
  <!-- out:readme.json#mass_gap_pts|.1f -->0.3<!-- /out --> points. Under trail braking, the friction ellipse and the
  normalized-slip model differ by at most <!-- out:readme.json#brake_model_gap_pts|.1f -->2.1<!-- /out --> points.
- **Direction: high.** Pro wins the lap at both grip scales. The closed-form optimum at 3.5 m is also pro. The
  omitted anti-side effects are small. See [Review notes](#review-notes).
- **Size: low.** The <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> grip scale is not validated. The same run repeats
  the map and the laps at <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out -->. The model is quasi-steady. It omits yaw
  damping, tire lag, roll steer, compliance steer, steer camber and aligning moment. The laps cover endurance and
  skidpad only.
- **Math.** Each trim solve accepts only a residual under 1e-7. `run.py` stops if a lap solve does not converge. A QSS
  lap on an 8 m circle differs from 2πR/v by <!-- out:readme.json#circle_check_error_pct|+.3f -->+0.072<!-- /out -->%.
  `run.py` stops above 0.5%.

## Feasibility with Front V33

![Ackermann against mean roadwheel steer for Front V33 and Orion, from hardpoints. Grey band = mean steer at the grip limit for R = 3.5 to 8 m](ackermann_curves.png)

Only this section uses the Front V33 hardpoints, through BobSim `kin_py`. Front V33 has
<!-- out:readme.json#base_ackermann_pct -->+42.5% to +45.9%<!-- /out --> Ackermann across the rack travel. That is at the packaging
limit set by the suspension lead. With full rack travel, the tightest CG radius at the grip limit is
<!-- out:readme.json#tightest_radius_m|.1f -->3.8<!-- /out --> m. The tightest legal CG path is near 3.5 m. The tie rod
outer point is <!-- out:readme.json#arm_offset_mm|.0f -->83<!-- /out --> mm from the kingpin axis. With this arm,
R = 3.5 m needs <!-- out:readme.json#rack_needed_mm|.1f -->34.5<!-- /out --> mm of rack.

The table shows linkages solved from the Front V33 hardpoints. Each moves the tie rod outer point and the rack pickup.
Hairpin steer is the mean roadwheel steer that a 3.5 m hairpin needs at the grip limit. Each linkage reaches its own
hairpin steer at <!-- out:readme.json#fix_rack_mm|.1f -->28.6<!-- /out --> mm of rack, which is 90% of travel. Each keeps
bump steer at zero at center. Toggle margin is how far the arm and the tie rod are from lining up.

<!-- out:linkage.md -->

| Option | Tie rod outer move | To wheel center plane | Arm to kingpin | Rack pickup move | Toggle margin at inner lock | Bump toe at inner lock, ±25 mm |
| ------ | ------------------ | --------------------- | -------------- | ---------------- | --------------------------- | ------------------------------ |
| Front V33 | – | 30 mm | 83 mm | – | 48° | +0.1° / −0.3° |
| -40% | 30 mm rearward, 31 mm inboard | 61 mm | 54 mm | 36 mm inboard, 3 mm down | 69° | +0.3° / −0.4° |
| 0% | 24 mm rearward, 20 mm inboard | 49 mm | 58 mm | 34 mm inboard, 4 mm down | 58° | +0.3° / −0.4° |
| +70% | 9 mm rearward, 13 mm outboard | 16 mm | 78 mm | 27 mm inboard, 6 mm down | 35° | +0.3° / −0.4° |

<!-- /out -->

## Review notes

**Reviewer.** The corner model has no speed term, so R = v²/(ay·g) is exact. The map reuses the 15 m value above 15 m.
The run balances LLTD once, with the parallel law at 15 m. This gives
<!-- out:readme.json#lltd_front_pct|.1f -->34.5<!-- /out -->% front at grip scale
<!-- out:readme.json#mu_nominal -->0.623<!-- /out -->. The envelope clips braking-assisted lateral grip at the steady value,
because BobSim QSS needs that. Every option gets unlimited steering lock in the comparison. Lock is a separate
feasibility gate. The skidpad uses the 9.125 m lane center. A tight CG path near 8.3 m is about 4.6% faster, but it
changes no lap change. Yaw derivatives are per roadwheel degree. The steering ratio is not in the comparison.

**Skeptic.** This is reasoning, not model output. The model omits four effects that favor anti: steer camber, transient
turn-in, compliance and aligning moment. Steer camber comes from 2.4° caster and 5.36° kingpin inclination (KPI).
Together the four add about 1° to 1.5°. At a 3.5 m hairpin the path-angle difference is near 6°. So they can shrink the
hairpin result, but they do not flip its direction. Pro-Ackermann roughly doubles the steering effort at the hairpin.
Confidence in this is low. Two cheap tests can check the model. Use Orion logs of yaw rate, speed and steer to measure
the real path-angle difference in hairpins. Run a toe A/B test on a 4 m circle. 0.5° toe-in against 1.0° toe-out is
about 15 Ackermann points.

## Method

BobSim's lap and dynamics models give both front wheels one steer angle, so `run.py` uses its own four-tire model. It
solves a quasi-steady corner at constant radius. Each wheel has its own path angle and an MF52 tire from BobSim. LLTD
splits the lateral load transfer. The solve balances forces and yaw moment, and keeps only stable states. Brake force
splits by brake bias. Drive force goes through the BobLib `Differential1D` LSD law. The map gives each option an
acceleration envelope for BobSim's QSS solver. For feasibility only, `kin_py` gives the Front V33 steering curve, and a
least-squares solve sets each linkage.

`run.py` takes `--mu`, `--ackermann`, `--bias` and `--quick`. Before a full run, run the `--quick` smoke test, which
runs every code path: `make study S=front-ackermann ARGS="--quick" V=smoke`.

## Inputs

| Input | Value | Source |
| ----- | ----- | ------ |
| Mass with driver, CG height, front weight | 263.1 kg (430 + 150 lb), 0.279 m (11 in), 45% | team 2027 estimate |
| Front track | 1270 mm | Front V33 |
| Brake bias, drive diff | 65% front, planned LSD (values in `run.py`) | assumptions |
| Tire, rear suspension | `16x7p5_10_12psi`, SHA-256 <!-- out:readme.json#tire_sha256 -->f38da74e929e4a1a<!-- /out -->…, Orion rear | Orion carryover |
| Grip scale | <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> and <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> | not validated |
| Track, power limit | Michigan 2019 endurance, 32 kW | BobSim lap config |
| Front hardpoints and rack, feasibility only | `2027_FrontV33.shk`, `FRONT SUSPENSION`, SHA-256 `31dea662…`, caster 2.4°, KPI 5.36°, rack travel 31.75 mm (1.25 in) each way | team |
| Ackermann limit | +<!-- out:readme.json#pack_pct -->42<!-- /out -->% | suspension lead, packaging |

## BobSim interfaces used

See the [bobsim-boundary skill](../../docs/skills/bobsim-boundary/SKILL.md).

- **Public.** `solve_qss_lap`, `GGVMap.from_arrays`, `optimize_racing_line`, `TrackCorridor`, `RacingLine`,
  `CornerKinematics`, `project_vehicle_yaml` and the `vehicle_io` loaders.
- **Private.** Only `_mf52_fx_pure` and `_mf52_fy_pure` from `_5_App.tire_eval`. BobSim has no public per-point tire
  API. A pin bump can break them. The public `GGVMap.from_arrays` replaced `_GGVSlice`.
- **Not usable here.** `dyn_py` and the transient lap give both front wheels one steer angle. The BobLib Modelica tier
  runs only maneuvers (steady state, ramp steer, step and sine steer, four-post), not a lap.

## Check before design freeze

1. **Steering lock and packaging.** Add rack travel or shorten the arm. Check clearances and toggle margin in CAD.
2. **Ackermann target.** If the best law is above the packaging limit, ask the suspension lead.
3. **Grip scale.** Validate it on track. It moves the size of every result.
4. **Path angle and toe.** Run the two cheap tests under [Review notes](#review-notes).
5. **Transient.** Run a Modelica step steer with the real linkage, once the model steers each front wheel separately.
6. **Model gaps.** Get the roll stiffness and add roll steer. Add an autocross track to BobSim upstream.
7. **Bias and diff.** Get the 2027 hydraulic bias and the LSD ramps and preload.
8. **Geometry.** `run.py` holds a copy of the Front V33 hardpoints. Copy any `.shk` change into it.

## Provenance

<!-- out:provenance.json -->

```json
{
  "study": "front-ackermann",
  "bobsim_sha": "4da577af1b04d86c53706eb6e80fb0064f71cee6",
  "vehicle_sha256": "3ab02bbf3e573aad0330bc37ce40fd254090d33dabd3760462c51da74e30afe8",
  "utc": "2026-10-09T21:10:48+00:00"
}
```

<!-- /out -->
