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
| `STUDY_VARIANT` | The variant name `V`. Empty for the main run. |
| `BOBSIM_SHA` | The pinned BobSim commit |
| `STUDY_WORKERS` | CPU limit for parallel cases. Empty means all CPUs. |

`make study` also passes `LHRE_SHA`, `LHRE_IMAGE_ID` and
`LHRE_REQUIRE_GIT` to `tools/provenance.py`. `run.py` does not need them.

Rules:

- Read the vehicle from `BOBSIM_VEHICLE`. Do not hardcode a path.
- Write every output to `OUT_DIR`. Write nothing else.
- To test a parameter change, load the vehicle, change the value in memory
  or in a copy under `OUT_DIR`, and pass that to BobSim.
- Import BobSim modules. For example:
  `from simulations.envelope.vehicle_loader import load_active_envelope_inputs`.
- Keep the script short. If you write a helper that other studies need, put
  it in `tools/` and import it as `tools.<module>`. Treat BobSim as a black
  box: do not change it for a study.

## Variants

A variant runs the same study with changed inputs, for example a higher
tire grip scale. `run.py` reads the inputs from its command line.

```bash
make study S=front-ackermann ARGS="--mu 0.75" V=mu075
```

- `ARGS` goes to `run.py`. `V` names the variant and is required with
  `ARGS`.
- The outputs go to `out/<name>--<V>/`. The README does not change.
- To compare, put the variant next to the main run: the same tables and
  values, for example `readme.json`, are in both folders.
- If a variant changes the answer, say so in the README, and keep the
  README run at the nominal inputs.

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

Write for a team member who did not run the study. Keep it short and
simple:

- Use Simplified Technical English: short sentences, active voice, one
  idea per sentence. See [AGENTS.md](../AGENTS.md#writing).
- Start the Result with the answer in three to five bullets. Put the
  detail after it.
- Give each number its unit, and say how sure you are.
- Keep each section to what a reader needs to act. Put the full method in
  `run.py`, not in prose. Aim for a README that a reader can finish in
  five minutes.
- Do not repeat a number in prose that a table already shows.

1. **Question.** One or two sentences.
2. **Method.** Tier, evaluation, the parameters you changed and their range.
3. **Result.** Numbers with units, and one or two figures. Say how sure you
   are and why.
4. **Provenance.** The BobSim SHA, the vehicle hash and the date, from
   `out/<name>/provenance.json`.

### Numbers from the run

`make study` fills the README from the run, so the numbers do not go stale.
Put a marker around each value. The markers are HTML comments, so the
published page shows only the value.

| Marker | Fills in |
| ------ | -------- |
| `<!-- out:provenance.json -->` … `<!-- /out -->` | The file in a code block. |
| `<!-- out:table.md -->` … `<!-- /out -->` | A Markdown file from `OUT_DIR`, as is. |
| `<!-- out:summary.json#a/b/c\|+.1f -->` … `<!-- /out -->` | One value from a JSON file. `/` separates keys. The text after `\|` is a Python format spec. |

- Put a block marker on its own line. Put a value marker inside a
  sentence.
- `run.py` writes the tables as `.md` files to `OUT_DIR`.
- `make study` also copies each figure that the README links, such as
  `![Result](result.png)`, from `out/<name>/` to the study folder.
- A marker that names a missing file or key stops `make study`. A value
  that is `null` or NaN also stops it. The README never shows `None`. Fix
  the marker or `run.py`.
- Words stay yours. Read the new numbers after each run, and change the
  text if the result changed.

## Provenance

`make study` writes `provenance.json` to `OUT_DIR` before `run.py` starts.
If `run.py` succeeds, `make study` moves `OUT_DIR` to `out/<name>/`. If it
fails, `out/<name>/` keeps the last good result, and the failed run stays in
`out/.staging/<name>/`.

The schema is `lhre.provenance.v2`. The file records:

- The study, the variant and the arguments of `run.py` (`argv`).
- The lhre, BobSim and BobLib commits and their dirty state. BobLib also
  has `gitlink`, the commit that the BobSim pin names.
- The image ID and the Python version.
- The vehicle path and SHA-256, the bundle ID and the SHA-256 of each tire
  file. It never copies tire data.
- The SHA-256 of each study file and each lhre tool that `run.py` imports
  (`study_code`).
- The invalidation keys (`invalidation`).

A value that the run cannot read is `null`, with the reason. The tool never
guesses it. In a git worktree, git in the container cannot read the
repositories. Then the commits come from the host, the dirty state is
`null`, and the BobSim and BobLib keys are `null`. With
`LHRE_REQUIRE_GIT=1`, as in CI, a `null` from git stops the run.

### Invalidation keys

Each key is a digest of the content of one input. A commit SHA is never a
key, so a commit that changes only docs does not change a key.

| Key | Input |
| --- | ----- |
| `vehicle` | The parsed values of the vehicle file |
| `tire:<name>` | The bytes of each `.tir` file that the vehicle names |
| `bundle_id` | `bundle_id` in `bundle.yml` next to the vehicle, or `absent` |
| `bundle_physics` | The values in `frame`, `axles`, `setup` and `torque_topology` of `bundle.yml`, or `absent` |
| `lhre:<path>` | Each file in the study folder, and each lhre module that the study imports |
| `bobsim:<folder>` | The files of each BobSim physics folder: `common`, `engines`, `simulations`, `optimization`, `Dockerfile` and `requirements.txt` |
| `bobsim:<file>` | Each BobSim module outside those folders that the study imports, for example `app/tire_eval.py` |
| `boblib:BobLib` | The files of the BobLib package, also the records that `make records` writes |

Python files are keyed by their tokens. YAML files are keyed by their
parsed values. A file that does not parse is keyed by its bytes.

These changes do not change a key:

- Markdown, reStructuredText, PDF and image files, in any folder.
- Comments, blank lines and indent width in Python files.
- Comments, layout, key order and number format (`280.0` or `280.00`) in
  YAML files.
- The `status` and `source` of a `{value, status, source}` value in a YAML
  file, for example in a study `inputs.yml`.
- BobSim `docs/`, `tests/`, `visualization/`, and `app/` modules that the
  study does not import.
- `__pycache__/` folders, and files that git ignores in BobSim and
  BobLib, such as build output.
- The `status` and `source` of a bundle value, and the bundle `status` and
  `release` fields.

The bundle next to the vehicle can also make a result stale. The stale
check reads it when it compares the keys:

- A bundle with `status: superseded` gives `STALE` (`bundle_status`).
- A bundle whose `members.vehicle.sha256` is not the SHA-256 of the vehicle
  file bytes gives `STALE` (`bundle_vehicle`). A bundle with no such hash
  gives `UNKNOWN`.

So a comment edit in the vehicle file keeps the `vehicle` key. It changes
the bytes, so update the bundle too. `make study` prints a warning when it
records a superseded bundle or a bundle that does not describe the vehicle.

### Check a result

```bash
docker compose run --rm -T sim python tools/invalidation.py stale out/<name>/provenance.json
```

The check compares the recorded keys with the current files. It uses the
vehicle in `BOBSIM_VEHICLE`, or the file that `--vehicle PATH` names. It
prints one of:

| Output | Exit code | Meaning |
| ------ | --------- | ------- |
| `CURRENT <name>` | 0 | Every key is the same. |
| `STALE <name>` and the changed keys | 1 | An input changed. Run the study again before you use its result. |
| `UNKNOWN <name>` and the keys it cannot compare | 3 | A key was `null` at the run or cannot be read now. Do not treat the result as current. |

A `provenance.json` without keys (schema version 1) is never `CURRENT`.
`CURRENT` means that the software inputs did not change. It is not a
hardware approval.
