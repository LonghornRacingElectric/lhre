import os
from concurrent.futures import ProcessPoolExecutor


def workers():
    return max(1, int(os.environ.get("STUDY_WORKERS") or os.cpu_count() or 1))


def map_cases(fn, cases):
    cases = list(cases)
    count = min(workers(), len(cases))
    if count <= 1:
        return [fn(case) for case in cases]
    with ProcessPoolExecutor(max_workers=count) as pool:
        return list(pool.map(fn, cases))
