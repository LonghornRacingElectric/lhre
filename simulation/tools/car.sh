#!/bin/sh
set -u

target=${1:-}
case $target in
    '' | -* | *[!A-Za-z0-9_.-]*)
        echo "make car: T='$target' is not a BobSim target name." >&2
        exit 2
        ;;
esac

out=out/car/$target
vehicle=../vehicle/vehicle.yml
results=bobsim/simulations/mbd/generated_results
four_post_config=bobsim/simulations/response/mbd/four_post/four_post_eval_config.yml
builds=bobsim/simulations/mbd/BuildBobLib
boblib_rel=${CAR_BOBLIB#bobsim/}

say() {
    printf '\n== make car: %s\n' "$*"
}

fact() {
    printf '%s=%s\n' "$1" "$2" >> "$out/.facts"
}

fail() {
    printf 'make car: %s\n' "$*" >&2
    fact failure "$*"
    exit 1
}

open_outputs() {
    $RUN sim sh -c 'if [ -d bobsim/simulations/mbd ]; then chmod -R a+rwX bobsim/simulations/mbd; fi'
}

bobsim_changes() {
    git -C bobsim status --porcelain --ignore-submodules=none
}

records_written() {
    if ! grep -Eq "^vehicle: +$CAR_VEHICLE_NAME\$" "$1"; then
        echo "make car: $1 does not name the vehicle $CAR_VEHICLE_NAME." >&2
        return 1
    fi
    if ! grep -Eq '^state: +written$' "$1"; then
        echo "make car: $1 does not report the records as written." >&2
        return 1
    fi
    outside=$(grep -E '^  (current|stale|missing) ' "$1" | grep -Ev "^  [a-z]+ +$boblib_rel/")
    if [ -n "$outside" ]; then
        printf 'make car: the generator names files outside %s:\n%s\n' "$CAR_BOBLIB" "$outside" >&2
        return 1
    fi
}

list_runs() {
    for run in "$builds"/*/results/run_*; do
        if [ -d "$run" ]; then
            echo "$run"
        fi
    done | sort
}

newer_records() {
    for file in $CAR_RECORD_FILES; do
        if [ -f "$CAR_BOBLIB/$file" ]; then
            find "$CAR_BOBLIB/$file" -newer "$1"
        fi
    done
}

finish() {
    status=$?
    trap - EXIT INT TERM HUP
    say "Restore bobsim/"
    restored=true
    git -c core.autocrlf=false -C bobsim checkout -- vehicle.yml || restored=false
    make records-undo || restored=false
    open_outputs || restored=false
    if [ "$restored" = false ]; then
        fact failure "Restoring bobsim/ failed."
        status=1
    fi
    if changes=$(bobsim_changes) && [ -z "$changes" ]; then
        fact bobsim_clean_after true
    else
        fact bobsim_clean_after false
        fact failure "bobsim/ has local changes after the run."
        printf 'make car: bobsim/ has local changes after the run:\n%s\n' "${changes:-git cannot read bobsim/}" >&2
        status=1
    fi
    rm -f "$out/.runs-before" "$out/.runs-after" "$out/.runs-new" "$out/.executables" "$out/.target-status"
    say "Write $out/identity.json"
    if ! $RUN sim python tools/car_identity.py "$out" && [ "$status" -eq 0 ]; then
        echo "make car: $out/identity.json is missing or marks the run as failed." >&2
        status=1
    fi
    exit "$status"
}

if ! changes=$(bobsim_changes); then
    echo "make car: git cannot read bobsim/." >&2
    exit 1
fi
if [ -n "$changes" ]; then
    echo "make car: bobsim/ has local changes. Run make car on the pin as committed." >&2
    echo "If an earlier make car stopped early, run 'git -C bobsim checkout -- vehicle.yml' and 'make records-undo'." >&2
    printf '%s\n' "$changes" >&2
    exit 1
fi

rm -rf "$out" && mkdir -p "$out" || exit 1
: > "$out/.facts"
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

fact target "$target"
fact vehicle_name "$CAR_VEHICLE_NAME"
fact records_state not_run
fact lhre_commit "$(git -C .. rev-parse HEAD 2>/dev/null)"
if lhre_changes=$(git -C .. status --porcelain -- vehicle simulation 2>/dev/null); then
    if [ -z "$lhre_changes" ]; then fact lhre_dirty false; else fact lhre_dirty true; fi
fi
fact bobsim_commit "$(git -C bobsim rev-parse HEAD)"
fact boblib_commit "$(git -C "$CAR_BOBLIB" rev-parse HEAD 2>/dev/null)"
image=$($COMPOSE config --images sim 2>/dev/null | head -n 1)
fact image_name "$image"
fact image_id "$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null)"

say "Write the Modelica records for vehicle/vehicle.yml"
fact records_state failed
make records > "$out/records.log" 2>&1
rc=$?
cat "$out/records.log"
[ "$rc" -eq 0 ] || fail "make records failed with exit status $rc."
records_written "$out/records.log" || fail "make records did not write the records for $CAR_VEHICLE_NAME."

say "Install vehicle/vehicle.yml as bobsim/vehicle.yml"
cp "$vehicle" "$out/vehicle.yml" && cp "$out/vehicle.yml" bobsim/vehicle.yml || fail "Cannot copy $vehicle to bobsim/vehicle.yml."
$RUN sim sh -c 'cd bobsim && python -m app.modelica_generator vehicle.yml' > "$out/records-check.log" 2>&1
rc=$?
cat "$out/records-check.log"
[ "$rc" -eq 0 ] || fail "The generator check of bobsim/vehicle.yml failed with exit status $rc."
records_written "$out/records-check.log" || fail "The records do not match the installed bobsim/vehicle.yml."
fact records_state written

say "Delete old results in $results"
$RUN sim sh -c "if [ -d $results ]; then find $results -mindepth 1 ! -name .gitkeep -exec rm -rf {} +; fi" \
    || fail "Cannot delete the old results in $results."
list_runs > "$out/.runs-before"

say "Run make bobsim T=$target"
fact target_status not_run
{
    make bobsim T="$target"
    echo $? > "$out/.target-status"
} 2>&1 | tee "$out/target.log"
target_status=$(cat "$out/.target-status")
fact target_status "$target_status"

say "Let the host read the BobSim outputs"
open_outputs || fail "Could not make bobsim/simulations/mbd readable on the host."

say "Copy the outputs to $out"
if [ -d "$results" ] && { ! [ -r "$results" ] || ! [ -x "$results" ]; }; then
    fail "The host cannot read $results."
fi
if [ -f "$four_post_config" ]; then
    cp "$four_post_config" "$out/"
fi
if [ -d "$results" ]; then
    mkdir -p "$out/generated_results"
    find "$results" -mindepth 1 -maxdepth 1 -type f ! -name .gitkeep ! -name '*.tir' -exec cp {} "$out/generated_results/" \;
fi
list_runs > "$out/.runs-after"
comm -13 "$out/.runs-before" "$out/.runs-after" > "$out/.runs-new"
: > "$out/.executables"
while IFS= read -r run; do
    build=${run%/results/*}
    dest=$out/runs/${build##*/}/${run##*/}
    mkdir -p "$dest"
    for name in manifest.json overrides.txt run.log; do
        if [ -f "$run/$name" ]; then
            cp "$run/$name" "$dest/"
        fi
    done
    find "$run" -maxdepth 1 -type f -name '*_res.csv' -size -1024k -exec cp {} "$dest/" \;
    echo "$build/BobLib.Experiments.Standards.${build##*/}" >> "$out/.executables"
done < "$out/.runs-new"

stale=0
for exe in $(sort -u "$out/.executables"); do
    if [ -f "$exe" ] && [ -z "$(newer_records "$exe")" ]; then
        fact executable "$exe built_after_records"
    else
        fact executable "$exe stale"
        echo "make car: $exe is stale. It is older than the records of $CAR_VEHICLE_NAME." >&2
        stale=1
    fi
done
[ "$stale" -eq 0 ] || fail "A stale executable ran. Delete $builds and run make car again."
[ "$target_status" -eq 0 ] || fail "make bobsim T=$target failed with exit status $target_status."
