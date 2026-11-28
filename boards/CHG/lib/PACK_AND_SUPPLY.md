# Pack reference and supply budget

The requested pack is **137s4p Molicel P30B: 548 cells**. The [Molicel P30B product data sheet, version 1.0](https://www.molicel.com/wp-content/uploads/Product-Data-Sheet-of-INR-18650-P30B-80111-1.pdf) lists 3.0 Ah typical capacity, 3.6 V nominal voltage, 4.2 V charge voltage and 3 A standard charge current per cell. It also lists a 9 A maximum charge rating with a 60°C cut-off; that rating is not an operational setting in this project. Pack design, cooling, cell matching and BMS validation determine approved limits.

| Derived pack reference | Value |
| --- | ---: |
| Cell count | 548 |
| Nominal voltage | 493.2 V |
| Sum of cell charge-voltage ratings | 575.4 V |
| Typical capacity | 12 Ah |
| Typical nominal energy | 5918.4 Wh / 5.9184 kWh |
| Pack current corresponding to cell standard charge current | 12 A |
| DC power at 493.2 V and 12 A | 5918.4 W |
| DC current for 6600 W at 493.2 V | 13.382 A |
| DC current for 6600 W at 575.4 V | 11.470 A |

These are reference calculations, not permission to charge. **575.4 V is not automatically an approved charger target.** The controller must use the team's approved pack profile, cell-level limits and current BMS charge-current limit. A cell can reach its limit before the pack reaches its summed ceiling. At 12 A, the budget cannot supply 6.6 kW below 550 V. Constant-current/constant-voltage taper, balancing and thermal derating can reduce power further.

SOC is remaining charge fraction; SOE is remaining usable energy fraction. Send both from the validated BMS/STM32 estimation system. Do not equate the two or infer either from pack voltage alone. Nominal energy is not a calibrated usable-energy measurement. ETA should come from the controller's charging/taper model, and be unknown if that estimate is unavailable.

## Controller calculation

[`ChargeBudget.h`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/CHG/lib/src/ChargeBudget.h) is a standalone C99/C++11 helper with no Arduino dependency:

```c
chg_budget_inputs in = {0}; /* Populate every input with fresh controller data. */
chg_budget_result result = chg_budget_calculate(&in);
/* result.valid == 0, or result.allowed_dc_w == 0: do not permit charging. */
```

Inputs use volts, amps and watts, not protocol millivolts/milliamps. The helper computes:

```text
AC-derived DC limit = max(0, Vrms × Iallowed × power_factor − auxiliary_AC_W) × efficiency
allowed DC watts = min(6600, AC-derived DC limit, Vpack × BMS_current_limit,
                       thermal_DC_limit, profile_DC_limit)
allowed DC amps = allowed DC watts / Vpack
```

The profile's voltage ceiling is independently enforced: at or above it the output is zero. Arbitrary pack profiles are supported; the helper has no built-in P30B voltage/current setting. All numeric inputs must be finite, voltage inputs positive, and power factor/efficiency in (0, 1]. Negative limits, invalid inputs, missing supply confirmation and overflow fail closed. Deliberate zero current/power limits produce a valid zero budget. Caller must also reject missing/stale data and enforce all charger/BMS/cell/interlock protections. Compile without `-ffast-math` so non-finite checks remain effective.

`supply_current_confirmed` must be exactly 1. **The screen does not detect outlet capacity.** The operator/controller must explicitly choose the allowed AC current for the actual circuit, upstream protection, wiring and other loads. Input voltage or a connector's shape does not establish available current. Charger input-voltage capability must also be validated by the controller; this arithmetic helper cannot establish it. A supply/profile change should require a stopped session, controller validation and fresh telemetry before a new start.

Power factor and efficiency are conservative validated values or current measured values. Account for uncertainty and margin upstream of this helper; a power budget alone does not regulate AC input current. Auxiliary watts represent other loads sharing this AC budget; if already included when setting `Iallowed`, avoid counting them twice. If other required inputs are unknown, supply a zero cap or invalidate readiness, rather than assuming a generous limit.

Illustrative arithmetic, **not approved outlet settings**, at PF 0.99, efficiency 0.95 and 50 W auxiliary load:

| Explicit example supply input | Available DC power before other caps |
| --- | ---: |
| 120 V, 12 A allowed | 1306.82 W |
| 240 V, 24 A allowed | 5369.78 W |
| 240 V, 32 A allowed | 7175.54 W, then capped to 6600 W or lower |

Telemetry should report the final budget and limiting reason from the STM32. Reason constants 2–6 align with the existing link protocol; `CHG_BUDGET_PROFILE` (7) is helper-local. Map it to `LimitBms` (4) for a pack/BMS-derived cap or `LimitCharger` (3) for a configured charger cap, according to the approved profile's actual source. Never transmit unsupported enum 7 as-is. The UI renders limits; only the STM32 applies them to charger hardware.

This numerical helper is supplied for controller integration. It is not automatically connected to the STM32 hooks, charger output or UI telemetry. The team's controller implementation must call it with validated fresh inputs, enforce its result and publish the actual applied limit. It does not implement interlocks, a cell-voltage supervisor, a thermal policy, AC protection or a charging state machine.

## Native verification

Run from the project root:

```sh
bazel test --config=local //boards/CHG/lib:budget_test
```

Tests cover supply-limited operation, BMS and temperature limits, arbitrary pack profiles, voltage ceiling, all non-finite/negative input positions, invalid zero parameters, intentional zero caps, numerical overflow and a sweep verifying every output remains within all constraints.
