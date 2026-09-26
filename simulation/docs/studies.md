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
| `STUDY_WORKERS` | CPU limit for parallel cases. Empty means all CPUs. |

Rules:

- Read the vehicle from `BOBSIM_VEHICLE`. Do not hardcode a path.
- Write every output to `OUT_DIR`. Write nothing else.
- To test a parameter change, load the vehicle, change the value in memory
  or in a copy under `OUT_DIR`, and pass that to BobSim.
- Import BobSim modules. For example:
  `from _2_EnvelopeSim.vehicle_loader import load_active_envelope_inputs`.
- Keep the script short. If you write a helper that other studies need, put
  it in `tools/` and import it as `tools.<module>`. Treat BobSim as a black
  box: do not change it for a study.

## Parallel cases

A study that solves many independent cases can run them in parallel.
`tools/parallel.py` gives `map_cases(fn, cases)`:

```python
from tools.parallel import map_cases

def solve(case):
    ...
    return rows

if __name__ == "__main__":
    results = map_cases(solve, cases)
```

- `map_cases` runs `fn` on each case in a separate process. It returns the
  results in the same order as `cases`, so the output does not change with
  the CPU count.
- `fn` must be a top-level function. Each case and each result must pickle.
  A lambda does not pickle. Use a small class with `__call__` instead.
- Put everything a case needs into the case. Do not rely on globals that
  `main()` sets.
- Write files only in the parent process. Workers return data.
- `make study S=<name> STUDY_WORKERS=1` runs the cases one at a time. Use it
  to debug.
- The container gets the CPUs that Docker Desktop allows. Change that in
  Docker Desktop under Settings, Resources.

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
