"""Compare the 11/16 machine benchmark: one row per machine from bench.sh output.

    python compare.py results/<label> [results/<label> ...]  ->  machines.md, results.csv
Each results/<label> holds machine.txt, vmstat.log, gpu.csv (if any) and gate/.
"""
import csv
import statistics as st
import sys
from pathlib import Path


def kv(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            out.setdefault(k.strip(), v.strip())
        elif ':' in line:
            k, v = line.split(':', 1)
            out.setdefault(k.strip(), v.strip())
    return out


def vmstat(path):
    busy, free = [], []
    header = None
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if 'id' in parts and 'us' in parts:
            header = parts
            continue
        if header and parts and parts[0].isdigit():
            row = dict(zip(header, parts))
            try:
                busy.append(100 - int(row['id']))
                free.append(int(row['free']) + int(row['buff']) + int(row['cache']))
            except (KeyError, ValueError):
                pass
    return busy[1:], free  # the first vmstat line is the average since boot


def gpu(path):
    util, mem, temp = [], [], []
    p = Path(path)
    if not p.exists():
        return None
    for row in csv.reader(p.read_text().splitlines()):
        try:
            util.append(float(row[1].strip().split()[0]))
            mem.append(float(row[2].strip().split()[0]))
            temp.append(float(row[3].strip()))
        except (IndexError, ValueError):
            pass
    return util, mem, temp


def fmt(xs, f='{:.2f}'):
    return f'{f.format(min(xs))} to {f.format(max(xs))}' if xs else 'n/a'


rows, runs = [], []
for d in map(Path, sys.argv[1:]):
    m = kv(d / 'machine.txt')
    summary = (d / 'gate' / 'summary.md')
    verdict = summary.read_text().splitlines()[0].lstrip('# ') if summary.exists() else 'no summary'
    res = list(csv.DictReader((d / 'gate' / 'results.csv').open())) if (d / 'gate' / 'results.csv').exists() else []
    gate = [r for r in res if r['kind'] == 'gate']
    base = [r for r in res if r['kind'] == 'baseline']
    rtf = [float(r['sim_s']) / float(r['wall_s']) for r in gate if r['sim_s'] and r['wall_s'] and float(r['wall_s']) > 0]
    for r in res:
        runs.append({'machine': d.name, **{k: r[k] for k in ('id', 'kind', 'event', 'outcome', 'sim_s', 'wall_s', 'lap_time_s', 'max_cte', 'max_pos_error', 'passed')}})
    busy, free = vmstat(d / 'vmstat.log') if (d / 'vmstat.log').exists() else ([], [])
    total = int(m.get('mem_total_mb', 0) or 0)
    peak_used = (total - min(free) // 1024) if free and total else None
    g = gpu(d / 'gpu.csv')
    rows.append({
        'machine': d.name,
        'cpu': m.get('Model name', '?'),
        'threads': m.get('CPU(s)', '?'),
        'gpu': (m.get('gpu') or 'none').split(',')[0],
        'renderer': m.get('OpenGL renderer string', ''),
        'extra': m.get('EXTRA', ''),
        'verdict': verdict,
        'gate_passed': f"{sum(r['passed'] == 'True' for r in gate)}/{len(gate)}",
        'rtf': f"{st.mean(rtf):.2f} ({fmt(rtf)})" if rtf else 'n/a',
        'wall_min': f"{int(m.get('wall_s', 0)) / 60:.0f}" if m.get('wall_s') else 'running',
        'cpu_busy': f"{st.mean(busy):.0f}% avg, {max(busy)}% peak" if busy else 'n/a',
        'mem_peak': f"{peak_used / 1024:.1f} of {total / 1024:.0f} GB" if peak_used else 'n/a',
        'gpu_load': (f"{st.mean(g[0]):.0f}% avg, {max(g[0]):.0f}% peak, {max(g[1]) / 1024:.1f} GB, {max(g[2]):.0f} C"
                     if g and g[0] else 'n/a'),
        'max_ekf': f"{max(float(r['max_pos_error']) for r in gate if r['max_pos_error']):.3f}" if gate else 'n/a',
        'max_cte': f"{max(float(r['max_cte']) for r in gate if r['max_cte']):.3f}" if gate else 'n/a',
        # Main has no truth-stack baselines since #232; the gate's own autocross
        # lap times show whether laps depend on the machine (#201).
        'lap_s': fmt([float(r['lap_time_s']) for r in gate if r['event'] == 'autocross' and r['lap_time_s']], '{:.2f}'),
    })

cols = ['machine', 'cpu', 'threads', 'gpu', 'extra', 'verdict', 'gate_passed', 'rtf', 'wall_min',
        'cpu_busy', 'mem_peak', 'gpu_load', 'max_ekf', 'max_cte', 'lap_s']
lines = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
lines += ['| ' + ' | '.join(str(r[c]) for c in cols) + ' |' for r in rows]
Path('machines.md').write_text('\n'.join(lines) + '\n')
with open('results.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(runs[0]) if runs else ['machine'])
    w.writeheader()
    w.writerows(runs)
print('\n'.join(lines))
