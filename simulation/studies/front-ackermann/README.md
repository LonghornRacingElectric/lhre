# front-ackermann

## Question

Is pro-Ackermann or anti-Ackermann steering better for the 2027 car, and by how much? This study compares constant steering laws from −50% to +100% on one car, one tire and one track. It also checks what the Front V33 hardpoints can package. It is a screening study. It gives the direction and the sensitivities, not final numbers.

Prior work: the 2026-10-03 vault note (`30 Agent Memory/inbox/2026-10-03 Front Ackermann study for 2027 steering.md`, PRs #61 and #64) recommended +42%. It found that the steering lock is the first gate. Its lap sizes are superseded. The 2026-10-09 note (`2026-10-09 Pro vs anti Ackermann across the operating range.md`, an earlier run of PR #77) gave −50% at +0.27 s and +50% at −0.08 s per lap. This run supersedes those numbers (see [Method](#method)). Abishek Rathnakumar's plan (https://app.notion.com/p/3cee26707bba816e88ebd09800484a31) found a trend from anti at low steer to pro at high steer. It found the anti-roll bar (ARB) split to be first order. This study (PR #77) extends that work with constant laws that need no hardpoints. It adds a powered operating map, sensitivity laps at two LLTD values and a second brake bias, a lock table per law, trim coverage and a grid-convergence noise band. It agrees with the plan on pro at high steer and on the bar split as first order. It cannot test anti at low steer, because constant laws cannot test a variable law. The steady cells at 11 m and 15 m are parallel. No cell above 8 m moves by more than 0.3%, which the lap cannot resolve.

## Answer

- **Keep the Front V33 law at <!-- out:readme.json#base_ackermann_pct -->+42.5% to +46.4%<!-- /out -->.** In this screening model it ties parallel steer on the Michigan endurance lap. It gains time at 3.5 m and 4.5 m hairpins. Not pushing past about +50% is a packaging judgment (see [Steering lock and linkage](#steering-lock-and-linkage)), not a model result. The closed-form optimum at 3.5 m is higher (optimum table).
- **The endurance lap at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> is a tie** for <!-- out:readme.json#best_lap_laws -->+25%, +50%, +75%, +100%, Front V33<!-- /out -->. Each is within the <!-- out:readme.json#convergence_band_ms|.0f -->50<!-- /out --> ms noise band of parallel steer (lap table). Only −50% is resolved, at <!-- out:readme.json#anti50_lap_s|+.3f -->+0.145<!-- /out --> s. −25% sits at the band edge. At grip scale <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> every law is inside the band. The lap rejects strong anti. It does not by itself choose pro.
- **Tight corners set the direction.** The hairpin table gives the time per 180° hairpin at the powered steady limit, at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> only. At 3.5 m and 4.5 m, Front V33 and every pro law gain time. Every anti law loses time. Front V33 is within the band of the best law.
- **The sign of the 3.5 m steady grip change holds in every tested variant. Its size does not.** At the 3.5 m powered steady limit, +50% changes grip by <!-- out:readme.json#pro50_apex_3p5|+.1f -->+12.5<!-- /out -->% and −50% by <!-- out:readme.json#anti50_apex_3p5|+.1f -->−15.4<!-- /out -->%. The sign holds at both LLTD variants, at bias 0.84 and at grip scale <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out -->. At LLTD −0.10 the +50% gain is under 1% (sensitivity table). At grip scale <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> it is near the map's parallel threshold (`operating_map.csv`, not tabulated here).
- **Anti never beats parallel outside the band in any tested condition.** Across the variants the −50% lap range is <!-- out:readme.json#anti50_lap_range_s -->−0.02 to +0.29 s<!-- /out --> and the +50% range is <!-- out:readme.json#pro50_lap_range_s -->−0.10 to +0.03 s<!-- /out --> (sensitivity table). LLTD moves the lap result more than the grip scale does.
- **Anti wins the braking side of the map, and the lap does not show it.** At grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out -->, anti wins <!-- out:readme.json#anti_cells -->16<!-- /out --> of <!-- out:readme.json#map_cells -->54<!-- /out --> cells, all on the braking side between 3.5 and 11 m. Pro wins <!-- out:readme.json#pro_cells -->15<!-- /out --> and parallel <!-- out:readme.json#parallel_cells -->23<!-- /out --> (map table). On the lap, −50% loses time in braking zones (phase table). The envelope clips braking-assisted grip by up to <!-- out:readme.json#clip_anti50_g|.2f -->0.20<!-- /out --> g for −50% and <!-- out:readme.json#clip_pro50_g|.2f -->0.00<!-- /out --> g for +50%. So the lap may understate anti at corner entry. This run does not quantify that bias.
- **Steering lock is a gate for every law, and the current design does not clear it at 3.5 m.** Front V33 reaches <!-- out:readme.json#steer_at_travel_deg|.1f -->23.3<!-- /out -->° of mean roadwheel steer at full rack. A 3.5 m hairpin at the grip limit needs <!-- out:readme.json#steer_needed_3p5_deg|.1f -->25.5<!-- /out -->° (coasting apex solve). The gate does not bind on the Michigan line. The tightest CG radius at the limit is <!-- out:readme.json#tightest_radius_m|.1f -->3.8<!-- /out --> m and the tightest corner is <!-- out:readme.json#tightest_corner_m|.2f -->4.47<!-- /out --> m. Anti makes the gate harder. −50% needs the most mean steer of every law at the 3.5 m limit (lock table), with a minimum lock margin of <!-- out:readme.json#lock_margin_min_deg|.2f -->0.73<!-- /out -->°.

**Why not copy other cars?** Many cars in videos run anti-Ackermann or parallel steer. A video shows a steering law. It does not show the inputs that set the answer. In this study the result moves with the roll stiffness split. At LLTD −0.10 both tested laws tie. At LLTD +0.10 pro wins outside the band (sensitivity table). It moves with the grip scale. The 3.5 m steady gain for +50% is large at <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> and near zero at <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out -->. It moves with static toe. One degree of toe-out is worth about <!-- out:readme.json#points_per_deg_toe_3p5|.0f -->10<!-- /out --> Ackermann points at 3.5 m. So a parallel car with toe-out behaves part way toward a pro car. The path-angle term scales with front track over wheelbase, 1.270 m over 1.5494 m for this car. Inference: a video cannot resolve a few degrees of toe, the tire's load sensitivity, the bar split or the rack geometry. Another team's law is a data point about their car, tire and track mix. It is not evidence about ours. The answer here holds for this car and tire, not in general.

## Result

All results come from a screening model. Ackermann % is `100 · (cot δ_outer − cot δ_inner) · L / t`, with roadwheel steer δ, wheelbase L and front track t. Positive is pro, negative is anti and zero is parallel steer. A grip change is the change in maximum lateral g against parallel steer. A lap change is the change in lap time against parallel steer. Negative means faster. The options are constant laws from −50% to +100% in 25-point steps. All laws use the same car, tires, LLTD, drive diff and track, and no hardpoints. Front V33 shows where the current design sits. The map steady row, the laps, the hairpin times and the yaw derivatives use powered steady state. There, drive force through the planned limited-slip differential (LSD) balances tire drag. The coasting apex sweep has no longitudinal force balance. So the same law at the same radius gives a different gain in each.

### Steering lock and linkage

![Ackermann against mean roadwheel steer for Front V33 and Orion, from hardpoints. Grey band = mean steer at the grip limit for R = 3.5 to 8 m](ackermann_curves.png)

Only this section uses the Front V33 hardpoints, through BobSim `kin_py`. An independent hand solve reproduces the Front V33 table (reviewer notes). Front V33 has <!-- out:readme.json#base_ackermann_pct -->+42.5% to +46.4%<!-- /out --> Ackermann from 10 mm to the full 31.75 mm of rack. The comparison gives every law unlimited lock. Lock is a separate gate. Two solves give a Front V33 value at 3.5 m. The coasting apex solve gives <!-- out:readme.json#steer_needed_3p5_deg|.1f -->25.5<!-- /out -->°. That needs <!-- out:readme.json#rack_needed_mm|.1f -->34.5<!-- /out --> mm of rack with the <!-- out:readme.json#arm_offset_mm|.0f -->83<!-- /out --> mm arm, so it uses the V33 curve past the real travel. The powered map solve gives <!-- out:readme.json#steer_needed_3p5_powered_deg|.1f -->25.2<!-- /out -->°, the Front V33 row of the next table.

<!-- out:lock.md -->

| Option | Mean steer at the 3.5 m limit | Inner / outer |
| ------ | ----------------------------- | ------------- |
| +0% | 29.8° | 29.8° / 29.8° |
| -50% | 34.9° | 31.1° / 38.7° |
| -25% | 32.2° | 30.5° / 33.9° |
| +25% | 27.4° | 28.7° / 26.2° |
| +50% | 24.8° | 26.8° / 22.7° |
| +75% | 22.6° | 25.2° / 20.0° |
| +100% | 22.4° | 25.8° / 19.1° |
| Front V33 | 25.2° | 27.2° / 23.2° |

<!-- /out -->

The table gives the mean roadwheel steer each law needs at the 3.5 m powered steady limit. An anti law needs more lock than a pro law for the same hairpin. So it needs more rack travel or a shorter arm. The solver bounds the outer wheel at 41.2°, the end of the Front V33 `kin_py` table. That bound is a table property, not a physical lock. The minimum lock margin to it is <!-- out:readme.json#lock_margin_min_deg|.2f -->0.73<!-- /out -->° for −50%, against 11.4° for parallel and 16.7° for Front V33. Lock-limited cells: <!-- out:readme.json#lock_limited_nominal_count -->0<!-- /out --> at nominal grip, <!-- out:readme.json#lock_limited_count -->1<!-- /out --> in the sensitivity map (a −50% cell in the bias 0.84 variant). So the −50% results at 3.5 m sit at the edge of the solver's steer range.

The linkage table shows linkages solved from the Front V33 hardpoints. Each moves the tie rod outer point and the rack pickup. Each reaches its own hairpin steer at <!-- out:readme.json#fix_rack_mm|.1f -->28.6<!-- /out --> mm of rack (90% of travel) and keeps bump steer at zero at center. Toggle margin is how far the arm and the tie rod are from lining up. The +70% linkage spends margin and clearance that Front V33 keeps. Parallel keeps more margin than Front V33, but it is a design change. The suspension lead first set +42% as the packaging limit. No later value has a source.

<!-- out:linkage.md -->

| Option | Tie rod outer move | To wheel center plane | Arm to kingpin | Rack pickup move | Toggle margin at inner lock | Bump toe at inner lock, ±25 mm |
| ------ | ------------------ | --------------------- | -------------- | ---------------- | --------------------------- | ------------------------------ |
| Front V33 | – | 30 mm | 83 mm | – | 48° | +0.1° / −0.3° |
| -40% | 30 mm rearward, 31 mm inboard | 61 mm | 54 mm | 36 mm inboard, 3 mm down | 69° | +0.3° / −0.4° |
| 0% | 24 mm rearward, 20 mm inboard | 49 mm | 58 mm | 34 mm inboard, 4 mm down | 58° | +0.3° / −0.4° |
| +70% | 9 mm rearward, 13 mm outboard | 16 mm | 78 mm | 27 mm inboard, 6 mm down | 35° | +0.3° / −0.4° |

<!-- /out -->

### Operating map

![Left: coasting apex sweep, grip change by Ackermann and corner radius, band = range over 12 input cases, diamond = Front V33. Right: best constant law at each corner radius and braking or drive level, powered, red = anti, blue = pro](grip_vs_ackermann.png)

The right panel and the table give the best law at each operating point at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out -->. An operating point is one corner radius and one braking or drive level. Each cell shows the best law and its grip gain over parallel steer. "parallel" means that no law beats parallel steer by more than 0.1%. "only +50% and up" means that parallel steer holds no grip at that level, and only those laws do. "no grip" means that no law holds that level at that radius. F or R names the axle that limits the parallel car.

<!-- out:map.md -->

| Longitudinal | 3.5 m | 4.5 m | 6 m | 8 m | 11 m | 15 m |
| ------------ | ----- | ----- | --- | --- | ---- | ---- |
| 1.2 g braking | -25% (+2.6%) F | -50% (+3.1%) F | -50% (+2.1%) R | -50% (+0.9%) R | -50% (+0.3%) R | parallel R |
| 0.9 g braking | -25% (+1.7%) R | -50% (+2.1%) R | -50% (+1.0%) R | -50% (+0.3%) R | parallel R | parallel R |
| 0.6 g braking | -25% (+0.5%) R | -50% (+1.2%) R | -50% (+0.8%) R | -50% (+0.3%) R | parallel R | parallel R |
| 0.3 g braking | parallel R | -50% (+0.8%) R | -50% (+0.6%) R | -50% (+0.2%) R | parallel R | parallel R |
| steady | +75% (+13.4%) F | +50% (+5.5%) F | +75% (+1.8%) F | +75% (+0.3%) F | parallel F | parallel F |
| 0.15 g drive | +75% (+7.9%) F | +50% (+0.4%) R | parallel R | parallel R | +100% (+0.3%) F | +100% (+0.2%) F |
| 0.3 g drive | +75% (+1.9%) R | +50% (+0.3%) R | parallel R | parallel R | parallel R | parallel R |
| 0.6 g drive | +75% (+2.6%) R | +50% (+0.3%) R | parallel R | parallel R | parallel R | parallel R |
| 0.9 g drive | only +50% and up | +75% (+8.2%) R | +50% (+0.4%) R | parallel R | parallel R | parallel R |

<!-- /out -->

Pro wins the steady and drive cells at 3.5 to 4.5 m. Anti wins 16 of the 24 braking cells between 3.5 and 11 m. The 3.5 m, 0.3 g cell and the three lighter 11 m cells are parallel. Parallel wins 15 of the 18 cells at 11 m and 15 m. The three others move by at most 0.3%. The limiting-axle split is a trend, not a rule. Some 4.5 m drive cells that pro wins are rear-limited. Some hard-braking cells that anti wins are front-limited. The best law at the 3.5 m steady cell is <!-- out:readme.json#best_3p5_steady -->+75%<!-- /out --> at <!-- out:readme.json#best_3p5_steady_gain_pct|+.1f -->+13.4<!-- /out -->%. Under the hardest braking at 3.5 m it is <!-- out:readme.json#best_3p5_hard_brake -->-25%<!-- /out -->. The cell counts are for grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> only. The <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> map is in `operating_map.csv` and is not tabulated here. At that grip scale anti wins 16 cells again, now every braking cell from 3.5 to 6 m, by up to +3.9%. The 3.5 m steady gain for +50% is +0.2% and −50% is −1.5%, so the steady cell is near the parallel threshold.

Caps: the run applied <!-- out:readme.json#caps_count -->31<!-- /out --> envelope caps (`summary.json` `operating_map.caps`). At nominal grip the 0.9 g drive cell has no solution for +0%, −50%, −25% and +25% at 3.5 m, and for −50% at 4.5 m. The rear drive limit at 3.5 m is near 0.9 g for every law. Parallel and anti need 16 to 50 N more rear thrust than +50% there, from front scrub, and that puts them over the limit (diagnostic run, not in the outputs). So their drive envelope is capped at 0.6 g at those radii, and +50%, +75%, +100% and Front V33 are not. The cap check refills the failed cells with the best law's grip and re-solves every nominal lap. The largest lap change is <!-- out:readme.json#cap_check_max_ms|.1f -->0.0<!-- /out --> ms (`summary.json` `cap_check`). So the caps do not change the lap result. At bias 0.84 the braking envelope is capped at 0.9 g at every radius for all three laws, against a 1.18 g straight-line limit (bias table).

### Continuous track

BobSim's quasi-steady-state (QSS) lap solver runs each option on the Michigan 2019 endurance minimum-curvature line. The line is <!-- out:readme.json#track_length_m|.0f -->1989<!-- /out --> m long. It has <!-- out:readme.json#corners_under_15m -->42<!-- /out --> corners tighter than 15 m. The tightest corner radius is <!-- out:readme.json#tightest_corner_m|.2f -->4.47<!-- /out --> m. Each option gets its own acceleration envelope from the four-tire model, with the 32 kW endurance power limit. The skidpad lap is one steady lap at R = 9.125 m. There is no autocross track. Laps closer than the <!-- out:readme.json#convergence_band_ms|.0f -->50<!-- /out --> ms noise band are equal.

<!-- out:lap.md -->

| Option | Endurance lap, grip 0.623 | Endurance lap, grip 0.75 | Skidpad lap, grip 0.623 | Skidpad lap, grip 0.75 |
| ------ | ------------------------- | ------------------------ | ----------------------- | ---------------------- |
| -50% | +0.14 s | −0.01 s | +0.012 s | −0.001 s |
| -25% | +0.05 s | −0.01 s | +0.005 s | −0.001 s |
| +25% | +0.01 s | +0.01 s | −0.003 s | +0.001 s |
| +50% | −0.02 s | +0.02 s | −0.004 s | +0.001 s |
| +75% | −0.01 s | +0.03 s | −0.005 s | +0.002 s |
| +100% | 0.00 s | +0.04 s | −0.004 s | +0.002 s |
| Front V33 | −0.02 s | +0.01 s | −0.004 s | +0.001 s |

<!-- /out -->

Trim coverage: the run samples the parallel-steer lap and tries a four-tire trim at <!-- out:readme.json#coverage_demand_pct|.0f -->98<!-- /out -->% of each point's demand. A trim exists for <!-- out:readme.json#coverage_pct|.1f -->87.3<!-- /out -->% of that time: braking <!-- out:readme.json#coverage_braking_pct|.1f -->76.2<!-- /out -->%, steady <!-- out:readme.json#coverage_steady_pct|.1f -->100.0<!-- /out -->% and exits <!-- out:readme.json#coverage_exit_pct|.1f -->94.0<!-- /out -->%. At 100% of demand it is <!-- out:readme.json#coverage_100_pct|.1f -->62.6<!-- /out -->%, and at 95% it is <!-- out:readme.json#coverage_95_pct|.1f -->95.1<!-- /out -->%. For the −50% and +50% laps it is <!-- out:readme.json#coverage_anti50_pct|.1f -->87.1<!-- /out -->% and <!-- out:readme.json#coverage_pro50_pct|.1f -->88.1<!-- /out -->%. Coverage was tested for +0%, −50% and +50% at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> only. A failed trim is a lower bound, because the solver tries a finite set of starting guesses.

Clip: the envelope clips braking-assisted lateral grip at the steady limit, because BobSim QSS needs that. The clip binds in <!-- out:readme.json#clip_cells -->14<!-- /out --> of <!-- out:readme.json#clip_of -->384<!-- /out --> braking solves. It removes up to <!-- out:readme.json#clip_anti50_g|.2f -->0.20<!-- /out --> g for −50% and <!-- out:readme.json#clip_pro50_g|.2f -->0.00<!-- /out --> g for +50%. By law the largest clip is 0.19 g for −25%, 0.13 g for parallel, 0.05 g for +25% and 0.0 g for +50% and above (`summary.json` `clipped_braking`). The largest clip is on <!-- out:readme.json#clip_max_curve -->-50%<!-- /out -->. The lap may understate anti at corner entry. This run does not quantify the size of that bias.

The next table splits the endurance lap change at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> by phase and by corner radius. Exits include the straights. The column headers give the time share of each radius band. For every pro law and Front V33, each phase and band change is inside the noise band, so the split is not resolved. Only −50% is resolved. Its change is positive in every phase and band. The steady and over-15 m parts are each inside the band, so only the total is resolved. The last table gives the time change per 180° hairpin at the powered steady limit, at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> only.

<!-- out:where.md -->

| Option | Braking zones | Steady corners | Exits | Radius under 6 m (2% of lap) | Radius 6 to 15 m (23% of lap) | Radius over 15 m (75% of lap) |
| ------ | ------------- | -------------- | ----- | ---------------------------- | ----------------------------- | ----------------------------- |
| -50% | +0.05 s | +0.03 s | +0.07 s | +0.07 s | +0.06 s | +0.02 s |
| -25% | +0.02 s | +0.01 s | +0.03 s | +0.03 s | +0.02 s | +0.01 s |
| +25% | +0.01 s | −0.01 s | +0.01 s | −0.01 s | +0.01 s | 0.00 s |
| +50% | +0.01 s | −0.02 s | −0.01 s | −0.02 s | 0.00 s | 0.00 s |
| +75% | +0.01 s | −0.02 s | −0.01 s | −0.02 s | 0.00 s | 0.00 s |
| +100% | +0.02 s | −0.02 s | 0.00 s | −0.02 s | +0.01 s | +0.01 s |
| Front V33 | +0.01 s | −0.02 s | 0.00 s | −0.02 s | 0.00 s | 0.00 s |

<!-- /out -->

<!-- out:hairpin.md -->

| Option | 3.5 m | 4.5 m |
| ------ | ----- | ----- |
| -50% | +152 ms | +101 ms |
| -25% | +70 ms | +47 ms |
| +25% | −58 ms | −33 ms |
| +50% | −100 ms | −50 ms |
| +75% | −106 ms | −50 ms |
| +100% | −103 ms | −48 ms |
| Front V33 | −95 ms | −50 ms |

<!-- /out -->

### Sensitivity

The run repeats the −50% and +50% laps at LLTD −0.10 and +0.10 from the balanced value and at brake bias 0.84. Only those two laws were run in these variants. The table gives the endurance lap change and the 3.5 m map changes for each.

<!-- out:sensitivity.md -->

| Variant | −50% lap | +50% lap | +50% at 3.5 m steady | +50% at 3.5 m, hardest braking |
| ------- | -------- | -------- | -------------------- | ------------------------------ |
| LLTD -0.10 | −0.02 s | +0.03 s | +0.8% | −3.3% |
| LLTD +0.10 | +0.29 s | −0.10 s | +12.0% | −4.8% |
| bias 0.84 | +0.16 s | −0.01 s | +12.5% | no grip |

<!-- /out -->

LLTD moves the lap result more than the grip scale does. At LLTD +0.10 both laws leave the band and pro wins. At LLTD −0.10 both tested laws tie. At bias 0.84 the front locks first (bias table). −50% loses outside the band, and +50% stays inside it. In the bias 0.84 map the 1.2 g braking row has no grip for any law at any radius. +50% wins the 3.5 m braking cells at 0.9 g and 0.6 g by +14.2% and +13.4%. −50% loses them by −20.9% and −12.6%. It keeps only +0.2% to +0.7% at 4.5 to 8 m light braking (`operating_map_sensitivity.csv`, not tabulated here). At grip scale <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> every law ties on the lap. −50% changes it by <!-- out:readme.json#anti50_lap_sensitivity_s|+.2f -->−0.01<!-- /out --> s, +50% by <!-- out:readme.json#pro50_lap_sensitivity_s|+.2f -->+0.02<!-- /out --> s and Front V33 by <!-- out:readme.json#design_lap_sensitivity_s|+.2f -->+0.01<!-- /out --> s. The run re-balances the LLTD at that grip scale (`summary.json` `operating_map.lltd_front`). So part of the tie may come from the re-balance and not from the grip change alone. Mass, CG height, weight split and camber move the grip change by at most <!-- out:readme.json#mass_gap_pts|.1f -->0.3<!-- /out --> points. Under trail braking, the friction ellipse and the normalized-slip model differ by at most <!-- out:readme.json#brake_model_gap_pts|.1f -->0.5<!-- /out --> points. The tire load-sensitivity parameter PKY2 was not swept.

### Limit behavior

<!-- out:limit.md -->

| R | -50% | +0% | +50% | Front V33 |
| - | ---- | --- | ---- | --------- |
| 3.5 m | +44.8 / 27.6 | +22.4 / 35.2 | +4.5 / 38.2 | +5.0 / 39.3 |
| 4.5 m | +23.7 / 33.2 | +8.5 / 38.3 | −0.9 / 40.8 | −0.2 / 40.4 |
| 8 m | −0.4 / 42.2 | −2.6 / 45.3 | −3.1 / 45.5 | −3.1 / 45.7 |

<!-- /out -->

Each cell gives stability / steering. Both are yaw-moment derivatives ×1000 per roadwheel degree, divided by weight times wheelbase. Stability is the yaw moment per degree of sideslip. Positive is restoring. Steering is the yaw moment per degree of mean roadwheel steer, the steering authority. Each option runs at 90% of its own limit lateral g. The derivatives exclude yaw damping and tire lag. At 3.5 m the +50% law has <!-- out:readme.json#pro50_authority_ratio_3p5|.2f -->1.09<!-- /out --> times the steering authority of parallel steer. Inference: this may give a quicker response near the limit. Pro lowers the static stability near the limit, and anti raises it. At 4.5 m the +50% law and Front V33 go slightly negative. Inference from the reviewer notes: yaw damping is large at hairpin speed and is not in the model, so a slightly negative value is not instability. This is not tested.

### Apex sweep

The apex sweep coasts. The left panel and the table give its grip change by law and corner radius. Each cell gives the nominal value, then the range over 12 cases. The cases cover two tire cornering-stiffness scales, both slip signs and three LLTD values. At 3.5 m every pro law gains and every anti law loses in all 12 cases. At 4.5 m and above the pro bands cross zero, so that claim holds only at 3.5 m.

<!-- out:apex.md -->

| R | -50% | -25% | +25% | +50% | +75% | +100% |
| - | ---- | ---- | ---- | ---- | ---- | ----- |
| 3.5 m | −12.5% (−13.8 to −11.6) | −6.0% (−6.4 to −5.0) | +5.2% (+3.1 to +5.9) | +8.8% (+4.0 to +10.6) | +10.5% (+3.4 to +13.0) | +10.4% (+2.4 to +13.0) |
| 4.5 m | −7.3% (−8.3 to −3.8) | −3.3% (−3.9 to −0.3) | +2.2% (−0.3 to +3.0) | +3.4% (−0.7 to +4.9) | +3.8% (−1.1 to +5.7) | +3.6% (−1.6 to +5.7) |
| 6 m | −2.2% (−3.4 to +0.4) | −0.8% (−1.4 to +0.2) | +0.4% (−0.2 to +1.0) | +0.6% (−0.5 to +1.5) | +0.4% (−0.7 to +1.8) | +0.5% (−0.9 to +1.9) |
| 8 m | −0.3% (−1.0 to +0.2) | −0.1% (−0.4 to +0.1) | +0.1% (−0.1 to +0.3) | +0.1% (−0.2 to +0.5) | −0.2% (−0.3 to +0.6) | −0.3% (−0.4 to +0.7) |
| 15 m | 0.0% (−0.1 to +0.1) | 0.0% (−0.1 to 0.0) | −0.1% (−0.1 to +0.1) | −0.1% (−0.1 to +0.1) | −0.2% (−0.2 to +0.2) | −0.2% (−0.2 to +0.2) |

<!-- /out -->

The next table checks the solver against a closed-form optimum for a front-limited car. The hand calculation comes from the reviewer notes (L 1.5494 m, t 1.270 m, CG 0.852 m behind the front axle). The front path-angle difference is 4.92° at 3.5 m, 3.01° at 4.5 m, 0.96° at 8 m, 0.28° at 15 m and 0.02° at 50 m. Load sensitivity on this tire asks for 0.44° more slip on the outer tire at 3.5 m. The two cross near 11.9 m. So pro at hairpins, because path geometry beats load sensitivity. Near parallel in open corners, because both are under 1°.

<!-- out:optimum.md -->

| R | Front-limited optimum | Solver optimum (10% steps) | +75% limited by |
| - | --------------------- | -------------------------- | --------------- |
| 3.5 m | 87% | +90% | front |
| 4.5 m | 86% | +80% | rear |

<!-- /out -->

### Brake bias and toe

`vehicle.yml` sets 84% front bias with no source. The study uses 65% front as the nominal case and 84% as a variant. The best straight-line bias is <!-- out:readme.json#ideal_bias_pct|.0f -->68<!-- /out -->% front, with a <!-- out:readme.json#ideal_limit_g|.2f -->1.54<!-- /out --> g limit.

<!-- out:bias.md -->

| Front bias | Locks first | Straight-line limit |
| ---------- | ----------- | ------------------- |
| 65% | rear | 1.46 g |
| 84% | front | 1.18 g |

<!-- /out -->

The toe table gives the best law at each total static toe-out. One degree of toe-out replaces about <!-- out:readme.json#points_per_deg_toe_3p5|.0f -->10<!-- /out --> points of Ackermann at 3.5 m. Toe-out recovers part of a pro law, not all of it (`summary.json` `toe`). Toe-out also adds scrub on the straights.

<!-- out:toe.md -->

| Total toe-out | 3.5 m best | 4.5 m best |
| ------------- | ---------- | ---------- |
| −1.0° (toe-in) | +100% | +100% |
| −0.5° | +90% | +90% |
| 0.0° | +90% | +80% |
| +0.5° | +80% | +70% |
| +1.0° | +70% | +60% |

<!-- /out -->

### Confidence

- **Direction: medium.** The closed form and the solver agree at 3.5 m. The sign of the 3.5 m steady grip change holds in every tested variant and in all 12 coasting apex cases. No tested condition makes anti faster than parallel outside the band. Against this: the model omits steer camber, transient turn-in, compliance steer and aligning moment. The skeptic's reasoning is that these favor anti and total 1° to 1.5°. The physics reviewer's hand estimate puts steer camber at 0.1° to 0.2° of equivalent slip, and the `.tir` has no camber thrust. Yaw damping is also omitted, and its sign for this question is not established. The clip bias against anti on the lap is not quantified. The cap check above shows that the caps do not change the lap.
- **Size: low.** The grip scale and the LLTD are not validated. Together they move the 3.5 m steady gain for +50% from large to near zero, and the lap result from a tie to outside the band. Between the 2026-10-09 run and this run the lap result moved by more than the noise band. Inference: the LSD fix is the main cause (see [Method](#method)).
- **Noise band.** Halving the lateral-g step and adding mid speeds moves the lap changes of −50%, +50%, +75%, +100% and Front V33. Twice the largest move is the band: <!-- out:readme.json#convergence_band_ms|.0f -->50<!-- /out --> ms. It was computed at grip scale <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> for those five laws only. The <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> laps and the sensitivity laps use the coarse grid, and the band is assumed to apply to them. Every refinement moved −50% by +0.025 s and the four pro laws by about −0.010 s, both away from parallel (`summary.json` `grid_convergence`).
- **Math.** Each trim solve accepts only a residual under 1e-7. `run.py` stops if a lap solve does not converge. A QSS lap on an 8 m circle differs from 2πR/v by <!-- out:readme.json#circle_check_error_pct|+.3f -->+0.050<!-- /out -->%. `run.py` stops above 0.5%. Envelope gaps: <!-- out:readme.json#gap_count -->0<!-- /out -->.

## Method

BobSim's lap and dynamics models give both front wheels one steer angle, so `run.py` uses its own four-tire model. It solves a quasi-steady corner at constant radius. Each wheel has its own path angle and an MF52 tire from BobSim. LLTD splits the lateral load transfer. The solve balances forces and yaw moment, and keeps only stable states. Brake force splits by brake bias. The map gives each option an acceleration envelope for BobSim's QSS solver. For feasibility only, `kin_py` gives the Front V33 steering curve, and a least-squares solve sets each linkage.

Drive force follows the BobLib `Differential1D` law through `lsd_split`. It solves the locked wheel speed. When the torque difference exceeds the clutch capacity it slips at that capacity toward the wheel the locked state favors. The committed code before this change sent the full capacity to the inner rear wheel at all times. That is observed in the code. Its effect on the lap size is an inference, because `run.py` changed in other ways between the two runs and no A/B lap of the diff alone exists.

`run.py` takes `--mu`, `--ackermann`, `--bias` and `--quick`. Before a full run, run the `--quick` smoke test, which runs every code path: `make study S=front-ackermann ARGS="--quick" V=smoke`.

## Inputs

| Input | Value | Source |
| ----- | ----- | ------ |
| Mass with driver, CG height, front weight | 263.1 kg (430 + 150 lb), 0.279 m (11 in), 45% | team 2027 estimate |
| Front track | 1270 mm | Front V33 |
| Front hardpoints and rack, feasibility only | `2027_FrontV33.shk`, `FRONT SUSPENSION`, SHA-256 `31dea662…`, caster 2.4°, KPI 5.36°, rack travel 31.75 mm (1.25 in) each way | Front V33 |
| Wheelbase, rear track, rear suspension | 1.5494 m, 1.2122 m, Orion rear | Orion carryover |
| Tire | `16x7p5_10_12psi`, SHA-256 <!-- out:readme.json#tire_sha256 -->f38da74e929e4a1a<!-- /out -->… | Orion carryover |
| Brake bias | 65% front nominal, 84% variant (`vehicle.yml` value, no source) | assumption |
| Drive diff | planned LSD, preload 5 Nm, lock 0.60, kinetic ratio 0.85 (`vehicle.yml` differs) | assumption |
| Grip scale | <!-- out:readme.json#mu_nominal -->0.623<!-- /out --> nominal, <!-- out:readme.json#mu_sensitivity -->0.75<!-- /out --> variant | not validated |
| LLTD | balanced in-model at 15 m with the parallel law, re-balanced per grip scale | assumption, not measured |
| Static toe and camber | 0 | assumption |
| Aero | none in the model (`vehicle.yml` has a downforce table) | omitted |
| Track, power limit | Michigan 2019 endurance, 32 kW, no autocross track | BobSim lap config |
| Packaging limit | +42% | suspension lead |

The balanced LLTD at the nominal grip scale is <!-- out:readme.json#lltd_front_pct|.1f -->34.4<!-- /out -->% front.

## BobSim interfaces used

See the [bobsim-boundary skill](../../docs/skills/bobsim-boundary/SKILL.md).

- **Public.** `solve_qss_lap`, `GGVMap.from_arrays`, `optimize_racing_line`, `TrackCorridor`, `RacingLine`, `CornerKinematics`, `project_vehicle_yaml` and the `vehicle_io` loaders.
- **Private.** Only `_mf52_fx_pure` and `_mf52_fy_pure` from `_5_App.tire_eval`. BobSim has no public per-point tire API. A pin bump can break them.
- **Not usable here.** `dyn_py` and the transient lap give both front wheels one steer angle. The BobLib Modelica tier runs only maneuvers, not a lap.

## Check before design freeze

1. **Roll stiffness split.** Measure the 2027 split, or at least the installed bar rates, and rerun at that LLTD. It is the input that moves the lap result the most.
2. **Grip scale.** Validate it on track. At the higher scale the hairpin gain is near zero.
3. **Steering lock and packaging.** Add rack travel or shorten the arm. Check clearances and toggle margin in CAD.
4. **Ackermann target.** Confirm the packaging limit with the suspension lead. Only +42% has a source.
5. **Path angle and toe.** Replay Orion hairpin logs (yaw rate, speed, steer) for the path-angle difference at 3.5 to 4.5 m. Run a toe A/B test on a 4 m circle: 0.5° toe-in against 1.0° toe-out, about 15 Ackermann points.
6. **Bias and diff.** Get the 2027 hydraulic bias and the LSD ramps and preload. Bias 0.84 removes most of anti's braking cells.
7. **Envelope.** Run an unclipped braking envelope to quantify the clip bias against anti.
8. **Transient.** Run a Modelica step steer with the real linkage, once the model steers each front wheel separately.
9. **Model gaps.** Add roll steer, aero and an autocross track upstream. Sweep PKY2.
10. **Geometry.** `run.py` holds a copy of the Front V33 hardpoints. Copy any `.shk` change into it.

## Provenance

<!-- out:provenance.json -->

```json
{
  "study": "front-ackermann",
  "bobsim_sha": "4da577af1b04d86c53706eb6e80fb0064f71cee6",
  "vehicle_sha256": "3ab02bbf3e573aad0330bc37ce40fd254090d33dabd3760462c51da74e30afe8",
  "utc": "2026-10-10T05:27:29+00:00"
}
```

<!-- /out -->
