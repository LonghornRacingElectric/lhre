# Sourced by run_gate.sh and run_faults.sh, so both read the gate stack the
# same way. GATE_STACK_FILE overrides the file, for tests.

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
