"""Keep saved edge-case worlds, launch branches and gate coverage consistent."""

import csv
import math
import os
from pathlib import Path
import subprocess

from launch import LaunchContext
from lhr_metrics.reference import (
    CircuitProgress, paired_centerline, ReferenceTrack, world_geometry)
from lhr_track_builder.track_builder_node import TrackBuilder
from lhr_trackgen.extreme_autocross import generate_extreme_track, SCENARIOS
from lhr_trackgen.publish_cones import ConePublisher, GENERATORS, pairing_width_m
from lhr_trackgen.rules_autocross import check_envelope
import pytest
from test_acceleration_event import _launch, _params
from test_launch_param_types import _start
from test_rules_autocross import load

PACKAGE = Path(__file__).parents[1]
TOOLS = Path(__file__).parents[4]/'testing/tools/gate'


@pytest.mark.parametrize('name', SCENARIOS)
def test_saved_world_is_current_legal_and_has_a_complete_reference(name, tmp_path):
    committed = PACKAGE/f'worlds/{name}_seed1.sdf'
    left, right, cones = world_geometry(committed)
    report = check_envelope(left, right)
    assert len(cones) == len(left)+len(right)
    expected = generate_extreme_track(name)
    for actual_side, expected_side in zip((left, right), expected):
        assert len(actual_side) == len(expected_side)
        assert max(math.dist(a, b) for a, b in zip(actual_side, expected_side)) < 1e-8
    if name in ('extreme_narrow', 'extreme_combined'):
        assert 3 <= report['minimum_clear_width_m'] < 3.01
    if name in ('extreme_hairpin', 'extreme_combined'):
        assert report['longest_straight_m'] == pytest.approx(80., abs=1e-8)
        assert report['minimum_radius_m'] == pytest.approx(4.5, abs=1e-8)
    if name == 'extreme_500m':
        assert 499.9999 < report['lap_length_m'] <= 500
    # Regenerate through the public CLI, not just the in-memory helper.
    output = tmp_path/'regenerated.sdf'
    subprocess.run(['python3', str(PACKAGE/'scripts/generate_world.py'),
                    '--style', name, '--output', str(output)], check=True, capture_output=True)
    regenerated = world_geometry(output)
    for actual_side, expected_side in zip(regenerated[:2], (left, right)):
        assert len(actual_side) == len(expected_side)
        assert max(math.dist(a, b) for a, b in zip(actual_side, expected_side)) < 1e-8
    track = ReferenceTrack(paired_centerline(left, right))
    progress = CircuitProgress(track)
    completed = False
    for i, (x, y) in enumerate(track.points+track.points[:2]):
        completed |= progress.observe(x, y, i*.4, 5.)
    assert completed
    assert not progress.invalid_reason


@pytest.mark.parametrize('name', SCENARIOS)
@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
def test_fixed_style_reaches_both_perception_branches(monkeypatch, name, perception, estimator):
    assert GENERATORS[name]() == generate_extreme_track(name)
    context, nodes = _launch(monkeypatch, track_style=name, perception=perception,
                             estimator=estimator)
    builder = _params(context, nodes, 'track_builder')
    width = pairing_width_m(name)
    assert builder['track_width'] == width
    assert _start(TrackBuilder, builder, ['track_width'])['track_width'] == width
    assert _params(context, nodes, 'metrics_node')['track_style'] == name
    if perception == 'sim':
        params = _params(context, nodes, 'publish_cones')
        assert _start(ConePublisher, params, ['track_style'])['track_style'] == name
    module = load('extreme_launch', PACKAGE/'launch/gazebo_demo.launch.py')
    launch_context = LaunchContext()
    launch_context.launch_configurations.update(track_style=name, seed='1')
    assert Path(module._find_world(launch_context)).name == f'{name}_seed1.sdf'


def plan(tmp_path, extremes):
    script = (TOOLS/'run_gate.sh').read_text().split('# Skidpad joins the matrix')[0]
    script = script.replace('HERE=$(cd "$(dirname "$0")" && pwd)', f'HERE="{TOOLS}"')
    env = dict(os.environ, SEEDS='', EXTREMES=extremes, REPEATS='2', BASELINE='1',
               ACCEL_REPEATS='0', SKIDPAD_REPEATS='0', OUT=str(tmp_path))
    return subprocess.run(['bash', '-c', script+'\nSKIDPAD_WORLD=/missing\nplan'],
                          env=env, text=True, capture_output=True)


def test_gate_plans_all_cases_and_keeps_matching_baselines(tmp_path):
    result = plan(tmp_path, 'all')
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 3*len(SCENARIOS)
    assert len({line.split()[0] for line in lines}) == len(lines)
    scorer = load('extreme_gate', TOOLS/'score_gate.py')
    for name in SCENARIOS:
        expected = [f'baseline_{name}_s1', f'{name}_s1_r1', f'{name}_s1_r2']
        actual = [line.split()[0] for line in lines if f'track_style:={name} ' in line]
        assert actual == expected
        row = scorer.score(str(tmp_path/'missing'), expected[1])
        assert row['event'] == 'autocross'
        assert row['track_style'] == name
        row.update(outcome='lap', stop_dist_m=1., finished=True, max_v_after_stop=0.,
                   max_cte=0., max_pos_error=0., off_track_count='0', cones_hit='0',
                   lap_time_s=100.)
        scorer.judge(row, {(name, '1'): 100., ('autocross', '1'): 10.})
        assert row['passed']
    # Every named planned row must fail if a campaign never actually ran it.
    (tmp_path/'expected.txt').write_text('\n'.join(line.split()[0] for line in lines))
    assert scorer.main(str(tmp_path)) == 1
    with (tmp_path/'results.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(lines)
    assert all(row['outcome'] == 'missing' for row in rows)
    assert all(row['passed'] == 'False' for row in rows if row['kind'] == 'gate')


def test_gate_accepts_subsets_and_rejects_unknown_names(tmp_path):
    subset = plan(tmp_path, 'extreme_hairpin extreme_500m')
    assert subset.returncode == 0
    assert len(subset.stdout.splitlines()) == 6
    invalid = plan(tmp_path, 'extreme_typo')
    assert invalid.returncode == 2
    assert 'unsupported extreme' in invalid.stderr
