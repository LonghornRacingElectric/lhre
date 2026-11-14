#!/usr/bin/env python3
"""Plot a cone-path replay's measured errors and a pre-contact vehicle-frame view."""

import argparse
import json
from pathlib import Path

from analyze_cone_path import local
from lhr_metrics.reference import paired_centerline, world_geometry
from lhr_vehicle import load_vehicle
import matplotlib
matplotlib.use('Agg')
from matplotlib.patches import Rectangle  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('analysis', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = np.genfromtxt(args.analysis / 'frames.csv', delimiter=',', names=True)
    summary = json.loads((args.analysis / 'summary.json').read_text())
    fig, axes = plt.subplots(3, 1, figsize=(10, 10), layout='constrained')
    for tag, label, color in [('estimated', 'Accumulated map, EKF poses', '#dc2626'),
                              ('truth', 'Same clouds, physics poses', '#15803d'),
                              ('instant', 'Current scan only, EKF poses', '#2563eb')]:
        axes[0].plot(rows['stamp_s'], rows[f'{tag}_local_map_error_p95_m'],
                     label=label, color=color)
    axes[0].set(yscale='log', ylabel='Local map error, per-frame p95 [m]',
                title='Same-cloud replay isolates pose-history distortion')
    axes[1].plot(rows['stamp_s'], rows['live_car_to_displayed_path_m'],
                 label='Estimated car to displayed path', color='#2563eb')
    axes[1].plot(rows['stamp_s'], rows['physical_centerline_cte_m'],
                 label='Physics car to physical course center', color='#dc2626')
    axes[1].set(ylabel='Cross-track distance [m]')
    axes[2].plot(rows['stamp_s'], rows['estimated_path_clearance_m'],
                 label='EKF-map path, projected into physical vehicle frame', color='#dc2626')
    axes[2].plot(rows['stamp_s'], rows['oracle_observed_path_clearance_m'],
                 label='Exact observed cone positions, same planner', color='#15803d')
    axes[2].axhline(0, color='black', linewidth=.7)
    axes[2].set(ylabel='Conservative 8 m horizon clearance [m]', xlabel='Simulation time [s]')
    for axis in axes:
        if summary['first_native_cone_contact_s'] is not None:
            axis.axvline(summary['first_native_cone_contact_s'], color='grey', linestyle='--')
        axis.grid(alpha=.2)
        axis.legend(fontsize=8)
        axis.set_xlim(7, rows['stamp_s'][-1])
    fig.savefig(args.out / 'cone-path-errors.png', dpi=150)
    plt.close(fig)

    snapshots = json.loads((args.analysis / 'snapshots.json').read_text())
    eligible = [s for s in snapshots if s['stamp_s'] < summary['cutoff_s']]
    target = min(20.5, summary['cutoff_s']-.1)
    snapshot = min(eligible, key=lambda s: abs(s['stamp_s']-target))
    true, estimated = snapshot['true_pose'], snapshot['estimated_pose']
    world = Path(__file__).parents[1] / 'worlds/autocross_seed3.sdf'
    left, right, _ = world_geometry(world)
    fig, ax = plt.subplots(figsize=(11, 7), layout='constrained')
    boundaries = [(left, '#2563eb', 'Physical blue cones'),
                  (right, '#ca8a04', 'Physical yellow cones')]
    for points, color, label in boundaries:
        points = local(points, np.asarray(true))
        ax.scatter(points[:, 0], points[:, 1], c=color, s=55, label=label)
    mapped = local(np.asarray(snapshot['estimated_map'])[:, :2], np.asarray(estimated))
    ax.scatter(mapped[:, 0], mapped[:, 1], marker='x', c='grey', s=35,
               label='Accumulated cone map relative to estimated car')
    for tag, color, label in [('estimated', '#dc2626', 'Path the controller sees locally'),
                              ('truth', '#15803d', 'Same-cloud path with physics registration')]:
        path = local(snapshot['physical_paths'][tag], np.asarray(true))
        ax.plot(path[:, 0], path[:, 1], color=color, linewidth=2, label=label)
    center = local(paired_centerline(left, right), np.asarray(true))
    ax.plot(center[:, 0], center[:, 1], ':', color='black', label='Physical course center')
    vehicle = load_vehicle()
    rear = vehicle.chassis_center_x_m-vehicle.body_length_m/2
    body = Rectangle(
        (rear, -vehicle.body_width_m/2), vehicle.body_length_m, vehicle.body_width_m,
        color='black', alpha=.2, label='Vehicle body')
    ax.add_patch(body)
    ax.scatter([0], [0], c='black', marker='*', s=90, label='Rear-axle / base_link origin')
    ax.set(xlim=(-3, 12), ylim=(-5, 5), xlabel='Forward from vehicle [m]',
           ylabel='Left of vehicle [m]', aspect='equal',
           title=f'Before contact: simulation {snapshot["stamp_s"]:.1f} s')
    ax.grid(alpha=.2)
    ax.legend(fontsize=8, loc='upper left')
    fig.savefig(args.out / 'cone-path-local-view.png', dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    main()
