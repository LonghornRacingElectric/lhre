"""
Time Orion's FMU on its own: milliseconds per 20 ms step, by driving phase.

Runs one FMU through fixed pedal phases with no Gazebo or stack, at a chosen
solver tolerance, and prints one JSON line. Run several copies at once to see
how per-step cost scales with parallel instances.

    python3 fmu_bench.py <tolerance> <label> <path/to/OrionFMI.fmu>

Needs a sourced workspace with lhr_sim_fmu (PR #221 or later) and fmpy.
"""
import json
import sys
import time

from lhr_sim_fmu import plant

TOLERANCE, LABEL, FMU = float(sys.argv[1]), sys.argv[2], sys.argv[3]
STEP_S = 0.02
# (name, steps, [steering, accelerator, brake])
PHASES = [('idle', 100, [0., 0., 0.]), ('accel', 250, [0., 0.15, 0.]),
          ('cruise', 150, [0., 0.08, 0.]), ('brake', 100, [0., 0., 0.3])]

# FmiSession fixes the tolerance at 1e-6; override it for this process only.
_init = plant.FmiSession.__init__


def _init_with_tolerance(self, path):
    from fmpy.fmi2 import FMU2Slave
    setup = FMU2Slave.setupExperiment

    def setup_with_tolerance(slave, startTime=0., tolerance=None, **kwargs):
        return setup(slave, startTime=startTime, tolerance=TOLERANCE, **kwargs)
    FMU2Slave.setupExperiment = setup_with_tolerance
    try:
        _init(self, path)
    finally:
        FMU2Slave.setupExperiment = setup


plant.FmiSession.__init__ = _init_with_tolerance

start = time.perf_counter()
session = plant.FmiSession(FMU)
out = {'label': LABEL, 'tol': TOLERANCE, 'setup_s': round(time.perf_counter() - start, 2)}
try:
    for name, steps, controls in PHASES:
        times = []
        for _ in range(steps):
            t0 = time.perf_counter()
            state = session.step(STEP_S, controls)
            times.append(time.perf_counter() - t0)
        out[name] = {'mean_ms': round(1000 * sum(times) / len(times), 1),
                     'max_ms': round(1000 * max(times), 1),
                     'v_end': round(state[3], 2)}
except Exception as error:  # report how far it got instead of a traceback
    out['error'] = str(error)[:160]
out['sim_s'] = round(session.time, 2)
print(json.dumps(out), flush=True)
