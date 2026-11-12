#!/usr/bin/env python3
"""Build a fixed competition world from ordered colored cones and bind its scoring route."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import yaml

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
for package in ('lhr_gazebo', 'lhr_metrics', 'lhr_trackgen', 'lhr_vehicle'):
    sys.path.insert(0, str(SOURCE/package))

from generate_world import _vehicle_nose_m, generate_world_sdf  # noqa: E402, I100
from lhr_gazebo.imported_tracks import (  # noqa: E402
    build_reference, checked_spawn, digest, PHYSICS_NAME, read_cones, SCHEMA)


def normalize_fssim(path, output):
    """Map ordered YAML side labels to current colors and remove closing duplicates."""
    source = yaml.safe_load(path.read_text())
    removed = []
    with output.open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['x', 'y', 'color'])
        for key, kind in (('cones_left', 'blue'), ('cones_right', 'yellow'),
                          ('cones_orange', 'orange_small'), ('cones_orange_big', 'orange_large')):
            points = source[key]
            if key in ('cones_left', 'cones_right') and points[0] == points[-1]:
                points = points[:-1]
                removed.append(key)
            for x, y in points:
                writer.writerow([format(x, '.17g'), format(y, '.17g'), kind])
    x, y, yaw = source['starting_pose_front_wing']
    nose = _vehicle_nose_m()
    return (x-nose*math.cos(yaw), y-nose*math.sin(yaw), yaw), removed


def main(argv=None):
    """Fail flagged geometry unless the caller explicitly requests a diagnostic world."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--format', choices=('csv', 'fssim'), default='csv')
    parser.add_argument('--name', required=True)
    parser.add_argument('--spawn', nargs=3, type=float, metavar=('X', 'Y', 'YAW'))
    parser.add_argument('--normalized-csv', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provenance', type=Path)
    parser.add_argument('--allow-out-of-envelope', action='store_true')
    parser.add_argument('--fit-spawn', action='store_true',
                        help='align at a clear reference pose within 5 m of requested spawn')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[a-z][a-z0-9_]*', args.name):
        parser.error('--name must match [a-z][a-z0-9_]*')
    spawn = args.spawn
    removed = []
    if args.format == 'fssim':
        if args.normalized_csv is None:
            parser.error('fssim needs --normalized-csv to retain the normalized source')
        args.normalized_csv.parent.mkdir(parents=True, exist_ok=True)
        derived, removed = normalize_fssim(args.source, args.normalized_csv)
        spawn = spawn or derived
        csv_path = args.normalized_csv
    else:
        csv_path = args.source
    if spawn is None or not all(map(math.isfinite, spawn)):
        parser.error('--spawn must supply three finite rear-axle map coordinates for CSV input')
    cones = read_cones(csv_path)
    reference = build_reference(cones, spawn)
    if reference['rules']['violations'] and not args.allow_out_of_envelope:
        parser.error(f'{reference["rules"]["violations"]}; '
                     'use --allow-out-of-envelope to retain a flagged world')
    alignment_spawn = list(spawn)
    spawn = checked_spawn(cones, reference, spawn, fit=args.fit_spawn)
    left = [(x, y) for x, y, kind in cones if kind != 'yellow']
    left_kinds = [kind for _, _, kind in cones if kind != 'yellow']
    right = [(x, y) for x, y, kind in cones if kind == 'yellow']
    sdf = generate_world_sdf(left, right, *spawn, left_kinds=left_kinds,
                             right_kinds=['yellow']*len(right), coordinate_precision=12)
    metadata = {
        'schema': SCHEMA, 'layout_id': args.name,
        'source_sha256': digest(args.source), 'normalized_csv_sha256': digest(csv_path),
        'source_file': args.source.name, 'spawn': list(spawn),
        'alignment_spawn': alignment_spawn, 'fit_spawn': args.fit_spawn,
        'removed_closing_vertices': removed,
        'reference_method': ('ordered monotone pairing; 0.5 m sampling; '
                             '2 m periodic Gaussian smoothing'),
        **reference,
        'provenance': json.loads(args.provenance.read_text()) if args.provenance else None,
    }
    reference_hash = hashlib.sha256(json.dumps(
        metadata, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    sdf = sdf.replace('name="default_physics"', f'name="{PHYSICS_NAME}_{reference_hash}"')
    metadata['world_sha256'] = hashlib.sha256(sdf.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sdf)
    args.output.with_suffix('.reference.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps({'world': str(args.output), **reference['rules']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
