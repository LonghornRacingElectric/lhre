# Studies

A study answers one engineering question about the current car. It is a
folder under `studies/` with two files:

```text
studies/<name>/
  README.md   # the question, the method and the result
  run.py      # the script that makes the result
```

Use a short kebab-case name that says the question, for example
`rear-stabar-rate` or `aero-balance-ggv`.

## run.py

`make study S=<name>` runs `run.py` in the container from `simulation/`.
The script gets these environment variables:

| Variable | Value |
| -------- | ----- |
| `BOBSIM_VEHICLE` | `/lhre/vehicle/vehicle.yml` |
| `OUT_DIR` | `out/.staging/<name>`. It exists before `run.py` starts. |
| `STUDY` | `<name>` |
| `BOBSIM_SHA` | The pinned BobSim commit |

Rules:

- Read the vehicle from `BOBSIM_VEHICLE`. Do not hardcode a path.
- Write every output to `OUT_DIR`. Write nothing else.
- To test a parameter change, load the vehicle, change the value in memory
  or in a copy under `OUT_DIR`, and pass that to BobSim.
- Import BobSim modules. For example:
  `from _2_EnvelopeSim.vehicle_loader import load_active_envelope_inputs`.
- Keep the script short. If you write a helper that other studies need, add
  it to BobSim.

## README.md

Use these sections:

1. **Question.** One or two sentences.
2. **Method.** Tier, evaluation, the parameters you changed and their range.
3. **Result.** Numbers with units, and one or two figures. Say how sure you
   are and why.
4. **Provenance.** Copy `out/<name>/provenance.json`: the BobSim SHA, the
   vehicle hash and the date.

## Provenance

`make study` writes `provenance.json` to `OUT_DIR` before `run.py` starts.
If `run.py` succeeds, `make study` moves `OUT_DIR` to `out/<name>/`. If it
fails, `out/<name>/` keeps the last good result, and the failed run stays in
`out/.staging/<name>/`.

```json
{
  "study": "<name>",
  "bobsim_sha": "7ff5191…",
  "vehicle_sha256": "8a06ba5…",
  "utc": "2026-09-23T07:30:31+00:00"
}
```

If the vehicle or the BobSim pin changes, run the study again before you
use its result.
