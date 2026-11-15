# Sourced by run_gate.sh and run_faults.sh, so both read the gate stack, and
# their own plan, the same way. GATE_STACK_FILE overrides the file, for tests.

checked_plan() {  # print the caller's plan(), or refuse: never half of one
    # plan() runs in a subshell, where set -u ends it at the first unset
    # variable (a stale name after a merge) and keeps what it printed so far.
    # Read straight into the run loop, that half plan ran and could score
    # PASS on the runs it kept, so it is read in full and checked first. An
    # unset name inside a $(...) in plan() only kills that inner subshell and
    # plan() carries on, so anything it writes to stderr is a refusal too.
    local lines status err
    err=$(mktemp) || return 2
    lines=$(plan 2>"$err")
    status=$?
    if [ "$status" != 0 ] || [ -s "$err" ] || [ -z "$lines" ]; then
        cat "$err" >&2
        rm -f "$err"
        echo "plan failed or is empty, so nothing was run" >&2
        return 2
    fi
    rm -f "$err"
    printf '%s\n' "$lines"
}

gate_stack() {  # print stack.args as one line of launch arguments
    local file=${GATE_STACK_FILE:-$(dirname "${BASH_SOURCE[0]}")/stack.args}
    local line args=''
    [ -r "$file" ] || { echo "gate stack: cannot read $file" >&2; return 1; }
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%%#*}
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [ -z "$line" ] && continue
        # The runners word-split the stack, so a value with a space would
        # silently become two arguments.
        case $line in
            *[[:space:]]*|*:=*:=*) ;;
            ?*:=?*) args="$args${args:+ }$line"; continue ;;
        esac
        echo "gate stack: not one launch argument: '$line' in $file" >&2
        return 1
    done < "$file"
    [ -n "$args" ] || { echo "gate stack: $file is empty" >&2; return 1; }
    printf '%s\n' "$args"
}
