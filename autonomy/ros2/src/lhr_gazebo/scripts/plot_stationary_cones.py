#!/usr/bin/env python3
"""Plot stationary pose error, observation scatter and persistent-map controls."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('analyses', type=Path, nargs='+')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.5), constrained_layout=True)
    for index, source in enumerate(args.analyses):
        summary = json.loads((source / 'summary.json').read_text())
        rows = np.genfromtxt(source / 'frames.csv', delimiter=',', names=True)
        label = Path(summary['capture']).name
        color = f'C{index % 10}'
        axes[0, 0].plot(rows['stamp_s'], rows['pose_error_m'], label=label, color=color)
        axes[0, 1].plot(rows['stamp_s'], rows['heading_error_deg'], label=label, color=color)
        axes[1, 0].plot(rows['stamp_s'], rows['estimated_count'], label=label, color=color)
        axes[1, 1].plot(rows['stamp_s'], rows['estimated_p95_m'], color=color)
        axes[1, 1].plot(
            rows['stamp_s'], rows['physics_p95_m'], linestyle='--', alpha=.45, color='black')
    axes[0, 0].set_ylabel('Position error (m)')
    axes[0, 1].set_ylabel('Absolute heading error (deg)')
    axes[1, 0].set_ylabel('Persistent map entries (replay)')
    axes[1, 1].set_ylabel('Local map nearest-cone p95 error (m)')
    for ax in axes.flat:
        ax.set_xlabel('Simulation time (s)')
        ax.grid(alpha=.25)
    axes[0, 0].legend(fontsize=8)
    axes[1, 1].text(.03, .97, 'Dashed: same clouds, physics registration',
                    transform=axes[1, 1].transAxes, va='top', fontsize=8)
    fig.suptitle('Stationary seed 3: physical motion verified independently; diagnostic trials')
    fig.savefig(args.out / 'stationary-timeseries.png', dpi=170)
    plt.close(fig)
    source = args.analyses[0]
    snapshots = json.loads((source / 'snapshots.json').read_text())
    snapshot = snapshots[-1]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.2), constrained_layout=True)
    truth = np.asarray(snapshot['true_local_cones'])
    for ax, key, title in zip(
            axes, ['estimated_map_in_vehicle', 'physics_map_in_vehicle'],
            ['Persistent map with EKF registration', 'Same scans with physics registration']):
        points = np.asarray(snapshot[key])
        ax.scatter(truth[:, 0], truth[:, 1], facecolors='none', edgecolors='black',
                   s=60, label='Physical cone centers')
        ax.scatter(points[:, 0], points[:, 1], s=14, label='Mapped cone entries')
        ax.scatter([0], [0], marker='^', color='red', label='Vehicle frame origin')
        ax.set(xlim=(-20, 22), ylim=(-21, 21),
               xlabel='Forward (m)', ylabel='Left (m)', title=title)
        ax.set_aspect('equal')
        ax.grid(alpha=.25)
    axes[0].legend(fontsize=8)
    summary = json.loads((source / 'summary.json').read_text())
    label = Path(summary['capture']).name
    fig.suptitle(f'{label}, t={snapshot["stamp_s"]:.1f}s; offline controls')
    fig.savefig(args.out / 'stationary-local-map.png', dpi=170)
    plt.close(fig)


if __name__ == '__main__':
    main()
