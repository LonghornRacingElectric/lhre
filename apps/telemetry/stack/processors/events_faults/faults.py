"""Fault detection and transition logic, independent of Kafka and protobuf."""

BOOLEAN_FAULTS = (
    "apps1_disconnect",
    "apps2_disconnect",
    "apps1_out_range",
    "apps2_out_range",
    "apps_mismatch",
    "apps_implause",
    "bpps1_disconnect",
    "bpps2_disconnect",
    "bpps1_out_range",
    "bpps2_out_range",
    "bpps_mismatch",
    "bse1_disconnect",
    "bse2_disconnect",
    "bse1_out_range",
    "bse2_out_range",
    "stomp_fault",
)


def detect_active_faults(diagnostics_high):
    """Return fault names; reject bitmasks that cannot be interpreted safely."""
    active = set()
    if diagnostics_high is None:
        return active

    for field in BOOLEAN_FAULTS:
        if diagnostics_high.get(field, False):
            active.add(field)

    for field in ("post_faults", "run_faults"):
        value = diagnostics_high.get(field, 0.0)
        try:
            mask = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Invalid {field}: {value!r}") from error
        # Above this range float32 can lose low bits. Treating invalid input as
        # an empty mask would incorrectly clear faults from the previous frame.
        if mask != value or not 0 <= mask < (1 << 24):
            raise ValueError(f"Invalid {field}: {value!r}")
        for bit in range(24):
            if mask & (1 << bit):
                active.add(f"{field}[{bit}]")
    return active


def diff_faults(previous, current, car, t_ms):
    """Emit each newly asserted/cleared fault once, in deterministic order."""
    return [
        {"kind": kind, "fault": fault, "car": car, "t_ms": t_ms}
        for kind, changes in (
            ("fault_raised", current - previous),
            ("fault_cleared", previous - current),
        )
        for fault in sorted(changes)
    ]
