#!/usr/bin/env python3
"""Combine scored gate campaigns with their original EKF error/provenance rows."""

import argparse
import csv
from pathlib import Path

CHECKS = ('completes', 'stops', 'off_course', 'cones', 'cte', 'ekf', 'lap_time', 'health')
FIELDS = ('campaign', 'id', 'imu_noise', 'gyro_bias', 'vehicle_sha256', 'track_style',
          'seed', 'imu_seed', 'outcome', 'duration_s',
          'mean_pos_error', 'max_pos_error', 'mean_yaw_error',
          'max_cte', 'cones_hit', 'off_track_count', 'passed', 'failed_checks')


def read_campaign(label, path):
    """Retain every scored gate row, including missing/incomplete measurements."""
    with (path / 'results.csv').open(newline='') as stream:
        scored = list(csv.DictReader(stream))
    for score in scored:
        if score['kind'] != 'gate':
            continue
        metrics = path / 'runs' / score['id'] / 'metrics.csv'
        original = {}
        if metrics.is_file():
            with metrics.open(newline='') as stream:
                records = list(csv.DictReader(stream))
                original = records[-1] if records else {}
        merged = {**original, **score, 'campaign': label}
        merged['failed_checks'] = ';'.join(c for c in CHECKS if score[c] != 'pass')
        yield {field: merged.get(field, '') for field in FIELDS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--campaign', action='append', required=True, metavar='LABEL=PATH')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for campaign in args.campaign:
        label, path = campaign.split('=', 1)
        rows.extend(read_campaign(label, Path(path)))
    with (args.out / 'comparison.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)

    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.patches import Patch
    import matplotlib.pyplot as plt

    labels = list(dict.fromkeys(row['campaign'] for row in rows))
    ids = list(dict.fromkeys(row['id'] for row in rows))
    fig, axes = plt.subplots(2, 1, figsize=(max(9, len(ids) * 1.2), 7), sharex=True)
    width = 0.8 / max(1, len(labels))
    if not rows:
        axes[0].text(0.5, 0.5, 'No scored gate rows', transform=axes[0].transAxes,
                     ha='center')
    for j, label in enumerate(labels):
        indexed = {row['id']: row for row in rows if row['campaign'] == label}
        positions = [i - 0.4 + width * (j + 0.5) for i in range(len(ids))]
        for axis, field in zip(axes, ('max_pos_error', 'mean_pos_error')):
            values = [float(indexed.get(run, {}).get(field) or 'nan') for run in ids]
            bars = axis.bar(positions, values, width, label=label)
            for bar, run in zip(bars, ids):
                row = indexed.get(run, {})
                if (row.get('passed', '').lower() != 'true' or row.get('failed_checks')
                        or row.get('outcome') not in ('lap', 'mission_finished')):
                    bar.set_hatch('///')
                    bar.set_edgecolor('black')
                    bar.set_linewidth(0.8)
    axes[0].axhline(0.5, color='red', linestyle='--', label='Gate limit: 0.5 m')
    axes[0].set_ylabel('Maximum position error (m)')
    axes[1].set_ylabel('Mean position error (m)')
    handles, names = axes[0].get_legend_handles_labels()
    axes[0].legend(handles + [Patch(facecolor='white', edgecolor='black', hatch='///')],
                   names + ['Partial / failed run'])
    for axis in axes:
        axis.grid(axis='y', alpha=0.25)
    axes[1].set_xticks(range(len(ids)), ids, rotation=35, ha='right')
    fig.suptitle('Native Gazebo IMU comparison — EKF tuning unchanged')
    fig.text(0.5, 0.01, 'Hatched bars: any failed gate check or partial run; inspect CSV.',
             ha='center', fontsize=9)
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    fig.savefig(args.out / 'position-error.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    main()
