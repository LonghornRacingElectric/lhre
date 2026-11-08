"""Legal courses reach world generation, launch, metrics and campaign scoring."""

import importlib.util
import math
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from lhr_metrics.reference import (
    CircuitProgress, paired_centerline, ReferenceTrack, world_geometry)
from lhr_track_builder.track_builder_node import TrackBuilder
from lhr_trackgen.publish_cones import ConePublisher, GENERATORS, pairing_width_m
from lhr_trackgen.rules_autocross import check_envelope
import pytest
from test_acceleration_event import _launch, _params
from test_launch_param_types import _start


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('seed', [1, 2, 3, 4, 5])
def test_world_reference_preserves_order_and_completes_lap(seed, tmp_path):
    module = load('rules_world', Path(__file__).parents[1]/'scripts/generate_world.py')
    left, right = GENERATORS['rules_autocross'](seed=seed, width_m=3.)
    world = tmp_path/'rules.sdf'
    world.write_text(module.generate_world_sdf(left, right))
    actual_left, actual_right, _ = world_geometry(world)
    check_envelope(actual_left, actual_right)
    center = paired_centerline(left, right)
    track = ReferenceTrack(paired_centerline(actual_left, actual_right))
    assert all(math.dist(a, b) < .0001 for a, b in zip(center, track.points))
    progress = CircuitProgress(track)
    completed = False
    for i, (x, y) in enumerate(track.points + track.points[:2]):
        completed |= progress.observe(x, y, i*.4, 5.)
    assert completed
    assert not progress.invalid_reason
    ground = ET.fromstring(world.read_text()).find('.//model[@name="ground_plane"]')
    size = float(ground.findtext('.//plane/size').split()[0])
    assert all(abs(v) < size/2 for p in left+right for v in p)


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('width, spacing', [('3.5', '2.0'), ('3', '1')])
def test_launch_dimensions_reach_nodes(monkeypatch, perception, estimator, width, spacing):
    context, nodes = _launch(monkeypatch, track_style='rules_autocross',
                             perception=perception, estimator=estimator,
                             width_m=width, cone_spacing_m=spacing)
    assert _params(context, nodes, 'metrics_node')['track_style'] == 'rules_autocross'
    params = _params(context, nodes, 'track_builder')
    expected = pairing_width_m('rules_autocross', float(width))
    assert params['track_width'] == expected
    assert _start(TrackBuilder, params, ['track_width'])['track_width'] == expected
    if perception == 'sim':
        params = _params(context, nodes, 'publish_cones')
        actual = _start(ConePublisher, params, ['width_m', 'cone_spacing_m', 'track_style'])
        assert actual == {'width_m': float(width), 'cone_spacing_m': float(spacing),
                          'track_style': 'rules_autocross'}


def test_gate_plan_and_scoring_keep_course_styles_separate(tmp_path):
    tools = Path(__file__).parents[4]/'testing/tools/gate'
    scorer = load('rules_gate', tools/'score_gate.py')
    for style in ('autocross', 'rules_autocross'):
        row = scorer.score(str(tmp_path/'missing'), f'{style}_s1_r1')
        assert row['event'] == 'autocross'
        assert row['track_style'] == style
        row.update(outcome='lap', stop_dist_m=1., finished=True, max_v_after_stop=0.,
                   max_cte=0., max_pos_error=0., off_track_count='0', cones_hit='0',
                   lap_time_s=50.)
        scorer.judge(row, {('autocross', '1'): 10., ('rules_autocross', '1'): 60.})
        assert row['lap_time'] == ('pass' if style == 'rules_autocross' else 'FAIL')
    # Evaluate the runner's real plan without launching or killing processes.
    script = (tools/'run_gate.sh').read_text().split('# Skidpad joins the matrix')[0]
    script = script.replace('HERE=$(cd "$(dirname "$0")" && pwd)', f'HERE="{tools}"')
    for style in ('autocross', 'rules_autocross'):
        # Truth-stack baselines are opt-in since the reference lap (#232).
        for baseline in ('', '1'):
            env = {k: v for k, v in os.environ.items() if k != 'BASELINE'}
            env.update(TRACK_STYLE=style, SEEDS='1', REPEATS='1', ACCEL_REPEATS='0',
                       SKIDPAD_REPEATS='0', OUT=str(tmp_path))
            if baseline:
                env['BASELINE'] = baseline
            result = subprocess.run(['bash', '-c', script+'\nSKIDPAD_WORLD=/missing\nplan'],
                                    env=env, text=True, capture_output=True, check=True)
            expected = ([f'baseline_{style}_s1 track_style:={style}'] if baseline else []
                        ) + [f'{style}_s1_r1 track_style:={style}']
            lines = result.stdout.splitlines()
            assert len(lines) == len(expected)
            for line, start in zip(lines, expected):
                assert line.startswith(start)


def test_course_reference_lap_comes_from_the_metrics_row(tmp_path):
    scorer = load('reference_gate', Path(__file__).parents[4]/'testing/tools/gate/score_gate.py')
    # A path from the machine that ran: the scorer finds the same world in the tree.
    metrics = {'reference_world': '/elsewhere/autocross_seed1.sdf', 'v_max': '5.0',
               'v_min': '1.0', 'a_lat_max': '3.0', 'max_accel': '1.0', 'max_decel': '2.0',
               'lap_gate_x_m': '18.109467', 'lap_gate_y_m': '-14.744176',
               'lap_gate_tangent_x': '0.589390', 'lap_gate_tangent_y': '0.807848'}
    assert scorer.course_reference(metrics, str(tmp_path)) == pytest.approx(33.91, abs=0.01)
    # Rows from before the Gazebo launch forwarded the limits logged zeros.
    assert scorer.course_reference(dict(metrics, v_max='0.0'), str(tmp_path)) is None
    row = {'id': 'autocross_s1_r1', 'event': 'autocross', 'track_style': 'autocross',
           'seed': '1', 'outcome': 'lap', 'stop_dist_m': 1., 'finished': True,
           'max_v_after_stop': 0., 'max_cte': 0., 'max_pos_error': 0., 'off_track_count': '0',
           'cones_hit': '0', 'early_exits': '', 'lap_time_s': '50', 'reference_lap_s': '40.00'}
    # The course reference wins over a baseline that would fail the lap.
    scorer.judge(row, {('autocross', '1'): 10.})
    assert (row['lap_reference'], row['lap_ratio'], row['lap_time']) == ('course', '1.25', 'pass')


def test_fault_runner_generates_the_requested_rules_world(tmp_path):
    tools = Path(__file__).parents[4]/'testing/tools/fault_injection'
    script = (tools/'run_faults.sh').read_text().split('\ncleanup\nfor kind')[0]
    script = script.replace('HERE=$(cd "$(dirname "$0")" && pwd)', f'HERE="{tools}"')
    env = dict(os.environ, OUT=str(tmp_path))
    result = subprocess.run(['bash', '-c', script+'\nworld_for rules_autocross 9'],
                            env=env, capture_output=True, text=True, check=True)
    world = Path(result.stdout.strip().removeprefix('world:='))
    assert world.name == 'rules_autocross_seed9.sdf'
    left, right, _ = world_geometry(world)
    check_envelope(left, right)
