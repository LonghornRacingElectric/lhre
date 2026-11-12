"""Competition inputs stay physical, reproducible and explicitly audited."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_gazebo.imported_tracks import (
    audit, build_reference, checked_spawn, COURSES, digest, read_cones, reference_curve)
from lhr_gazebo.world_cones import observation_cones, WorldCones
from lhr_lidar_sim.world import load_world_cones
from lhr_metrics.course import CourseEnvelope
from lhr_metrics.metrics_node import MetricsNode
from lhr_metrics.reference import (
    CircuitProgress, imported_reference, ReferenceTrack, world_geometry)
from lhr_metrics.reference_lap import reference_lap
from lhr_trackgen.rules_autocross import measure_route
from lhr_vehicle import load_vehicle
import pytest
import rclpy
import yaml

PACKAGE = Path(__file__).parents[1]
WORLDS = PACKAGE/'worlds'
GATE = PACKAGE.parents[2]/'testing/tools/gate'


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('name,counts', [('amz_fsg', (94, 88, 4)), ('amz_fsi', (80, 74, 4))])
def test_original_physical_cones_and_scoring_route(name, counts):
    source = WORLDS/'competition'/f'{name}.yaml'
    raw = yaml.safe_load(source.read_text())
    normalized = read_cones(source.with_suffix('.csv'))
    expected = []
    for key, kind in [('cones_left', 'blue'), ('cones_right', 'yellow'),
                      ('cones_orange', 'orange_small'), ('cones_orange_big', 'orange_large')]:
        points = raw[key]
        if key in ('cones_left', 'cones_right') and points[0] == points[-1]:
            points = points[:-1]
        expected += [(round(x, 12), round(y, 12), kind) for x, y in points]
    assert normalized == expected
    world = WORLDS/f'{name}.sdf'
    data = imported_reference(world)
    assert data['source_sha256'] == digest(source)
    assert data['normalized_csv_sha256'] == digest(source.with_suffix('.csv'))
    left, right, cones = world_geometry(world)
    assert sorted((x, y, kind) for _, x, y, _, kind in cones) == sorted(expected)
    kinds = ('blue', 'yellow', 'orange_large')
    assert tuple(sum(c[-1] == k for c in cones) for k in kinds) == counts
    rebuilt = build_reference(normalized, data['alignment_spawn'])
    assert rebuilt['pairs'] == data['pairs']
    for a, b in zip(rebuilt['centerline'], data['centerline']):
        assert a == pytest.approx(b, abs=1e-9)
    assert rebuilt['rules']['status'] == data['rules']['status'] == 'outside_envelope'
    raw_midpoints = [((left[i][0]+right[i][0])/2, (left[i][1]+right[i][1])/2)
                     for i in range(len(left))]
    assert rebuilt['rules']['geometry'] == 'unsmoothed_gate_midpoints'
    for key, value in measure_route(raw_midpoints).items():
        assert rebuilt['rules']['measurements'][key] == pytest.approx(value, abs=1e-8)
    assert len(left) == len(right) >= max(counts[:2])
    course = CourseEnvelope(left, right, cones, load_vehicle(), True)
    assert course.pose_check(*data['spawn'], padding=.05) == (False, set())
    reference = ReferenceTrack(data['centerline'])
    progress = CircuitProgress(reference, on_course=course.contains)
    completed = []
    for i, (x, y) in enumerate([*reference.points, *reference.points[:2]]):
        assert course.contains(x, y)
        if progress.observe(x, y, 1.+i*.1, 5.):
            completed.append(i)
    assert len(completed) == 1
    assert not progress.invalid_reason
    observations = observation_cones(world).markers
    assert len(observations) == len(expected)
    assert len(load_world_cones(world)) == len(expected)
    observed = [(m.pose.position.x, m.pose.position.y, m.text) for m in observations]
    assert sorted(observed) == sorted(expected)
    assert {m.ns for m in observations} == {'left_cones', 'right_cones', 'start_finish'}


@pytest.mark.parametrize('edit', ['missing', 'world', 'metadata', 'unmarked'])
def test_reference_binding_rejects_stale_or_missing_inputs(tmp_path, edit):
    world = tmp_path/'course.sdf'
    source = WORLDS/'amz_fsg.sdf'
    shutil.copyfile(source, world)
    sidecar = world.with_suffix('.reference.json')
    if edit != 'missing':
        shutil.copyfile(source.with_suffix('.reference.json'), sidecar)
    if edit == 'world':
        world.write_text(world.read_text()+'\n')
    elif edit == 'metadata':
        data = json.loads(sidecar.read_text())
        data['centerline'][0][0] += .1
        sidecar.write_text(json.dumps(data))
    elif edit == 'unmarked':
        world.write_text(world.read_text().replace('lhr_imported_reference_v1_', 'removed_'))
    with pytest.raises(ValueError):
        world_geometry(world)


@pytest.mark.parametrize('content', [
    'x,y,kind\n0,0,blue\n', 'x,y,color\nnan,0,blue\n',
    'x,y,color\n0,0,red\n', 'x,y,color\n0,0,blue\n0,0,yellow\n',
    'x,y,color\n0,0,blue\n',
])
def test_bad_csv_is_rejected(tmp_path, content):
    path = tmp_path/'bad.csv'
    path.write_text(content)
    with pytest.raises(ValueError):
        read_cones(path)


def launch(monkeypatch, **overrides):
    launch_file = module(PACKAGE/'launch/gazebo_demo.launch.py', 'competition_launch')
    monkeypatch.setattr(launch_file, 'get_package_share_directory', lambda _: str(PACKAGE))
    context = LaunchContext()
    for action in launch_file.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(track_style='amz_fsg', **overrides)
    return context, launch_file._launch_setup(context)


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
def test_imported_launch_and_node_parameters(monkeypatch, tmp_path, perception, estimator):
    context, actions = launch(
        monkeypatch, perception=perception, estimator=estimator,
        allow_noncompliant_track='true', output_csv=str(tmp_path/'metrics.csv'))
    nodes = {a.node_executable: a for a in actions if isinstance(a, Node)}
    assert ('world_cones' in nodes) == (perception == 'sim')
    assert 'publish_cones' not in nodes
    if perception == 'lidar':
        assert 'lidar_cone_detector' in nodes
    else:
        assert 'sensor_sim' in nodes
    metrics = evaluate_parameters(context, nodes['metrics_node']._Node__parameters)[0]
    assert metrics['track_style'] == 'amz_fsg'
    assert metrics['reference_world'] == str(WORLDS/'amz_fsg.sdf')
    params = [metrics]
    classes = [MetricsNode]
    if perception == 'sim':
        params.append(evaluate_parameters(context, nodes['world_cones']._Node__parameters)[0])
        assert type(params[-1]['world_sdf']) is str
        classes.append(WorldCones)
    for cls, parameters in zip(classes, params):
        args = ['--ros-args']
        for key, value in parameters.items():
            args += ['-p', f'{key}:={json.dumps(value)}']
        rclpy.init(args=args)
        node = None
        try:
            node = cls()
            if cls is MetricsNode:
                expected = imported_reference(WORLDS/'amz_fsg.sdf')
                assert node._reference.sha256 == ReferenceTrack(expected['centerline']).sha256
        finally:
            if node:
                node.destroy_node()
            rclpy.shutdown()


@pytest.mark.parametrize('overrides', [
    {}, {'seed': '2'}, {'world': str(WORLDS/'amz_fsi.sdf')},
])
def test_invalid_import_selection_fails(monkeypatch, overrides):
    with pytest.raises(ValueError):
        launch(monkeypatch, **overrides)


def test_importer_round_trip_and_explicit_flag(tmp_path):
    importer = module(PACKAGE/'scripts/import_world.py', 'competition_importer')
    world = tmp_path/'import.sdf'
    args = ['--source', str(WORLDS/'competition/amz_fsi.yaml'), '--format', 'fssim',
            '--normalized-csv', str(tmp_path/'input.csv'), '--name', 'test_fsi',
            '--output', str(world), '--fit-spawn']
    with pytest.raises(SystemExit):
        importer.main(args)
    assert not world.exists()
    assert importer.main([*args, '--allow-out-of-envelope']) == 0
    assert len(world_geometry(world)[2]) == 158


def gate_env(tmp_path):
    return {**os.environ, 'OUT': str(tmp_path/'campaign'), 'PLAN_ONLY': '1', 'SEEDS': '',
            'ACCEL_REPEATS': '0', 'SKIDPAD_REPEATS': '0', 'REPEATS': '2',
            'REAL_TRACKS': 'amz_fsg amz_fsi', 'ALLOW_NONCOMPLIANT': '1', 'BASELINE': '1'}


def test_audit_preserves_long_straights_and_tight_turns_before_smoothing():
    midpoints = [(0., 0.), (45., 0.), (90., 0.), (90., 20.), (45., 20.), (0., 20.)]
    left = [(x, y+2.) for x, y in midpoints]
    right = [(x, y-2.) for x, y in midpoints]
    smoothed = reference_curve(left, right, [(i, i) for i in range(len(midpoints))])
    assert measure_route(smoothed)['longest_straight_m'] < 80
    report = audit(left, right, midpoints)
    assert report['measurements']['longest_straight_m'] == pytest.approx(90.)
    assert 'straight exceeds 80 m' in report['violations']
    tight = [(0., 0.), (2., 0.), (2., 2.), (0., 2.), (-2., 2.), (-2., 0.)]
    report = audit(left, right, tight)
    assert report['measurements']['minimum_radius_m'] == pytest.approx(2**.5)
    assert 'reference turn radius below 4.5 m' in report['violations']


def test_gate_plan_has_matched_baselines_and_no_side_effects(tmp_path):
    env = gate_env(tmp_path)
    result = subprocess.run(['bash', str(GATE/'run_gate.sh')], env=env,
                            capture_output=True, text=True, check=True)
    lines = result.stdout.splitlines()
    assert len(lines) == 6
    for name in COURSES:
        assert any(s.startswith(f'baseline_{name}_s1 ') for s in lines)
        assert sum(s.startswith(name+'_s1_r') for s in lines) == 2
    assert all('allow_noncompliant_track:=true' in s for s in lines)
    for line in lines:
        if 'perception:=lidar' in line:
            assert 'lidar_source:=mid360' in line
            assert 'enable_stereo_camera:=true' in line
            assert 'enable_camera_lidar_fusion:=true' in line
    assert not Path(env['OUT']).exists()
    for name, allow in [('unknown', '1'), ('amz_fsg', '0')]:
        result = subprocess.run(['bash', str(GATE/'run_gate.sh')],
                                env={**env, 'REAL_TRACKS': name, 'ALLOW_NONCOMPLIANT': allow},
                                capture_output=True, text=True)
        assert result.returncode == 2
        assert not Path(env['OUT']).exists()


def test_scorer_recognizes_imports_and_fails_missing_runs(tmp_path):
    scorer = module(GATE/'score_gate.py', 'competition_gate_scorer')
    row = scorer.score(str(tmp_path/'missing'), 'amz_fsg_s1_r1')
    assert row['event'] == 'autocross'
    scorer.judge(row, {('amz_fsi', '1'): 50.})
    assert not row['passed']
    assert row['lap_time'] == 'FAIL'
    row.update(outcome='lap', lap_time_s='60', stop_dist_m=1., finished=True,
               max_v_after_stop=0., max_cte='.1', max_pos_error='.1',
               off_track_count='0', cones_hit='0')
    scorer.judge(row, {('amz_fsi', '1'): 50.})
    assert not row['passed']  # another layout cannot supply the baseline
    scorer.judge(row, {('amz_fsg', '1'): 50.})
    assert row['passed']


def test_course_lap_reference_uses_the_same_imported_route_as_metrics(tmp_path):
    scorer = module(GATE/'score_gate.py', 'imported_course_lap')
    data = imported_reference(WORLDS/'amz_fsg.sdf')
    route = ReferenceTrack(data['centerline'])
    limits = {'v_max': 5., 'v_min': 1., 'a_lat_max': 3., 'max_accel': 1., 'max_decel': 2.}
    _, _, point, tangent = route.project(*route.points[0])
    metrics = {**{key: str(value) for key, value in limits.items()},
               'reference_world': str(WORLDS/'amz_fsg.sdf'),
               'lap_gate_x_m': str(point[0]), 'lap_gate_y_m': str(point[1]),
               'lap_gate_tangent_x': str(tangent[0]), 'lap_gate_tangent_y': str(tangent[1])}
    expected = reference_lap(data['centerline'], start=point, tangent=tangent, **limits)
    assert scorer.course_reference(metrics, str(tmp_path)) == pytest.approx(expected)


def test_unsafe_source_spawn_requires_explicit_correction():
    cones = read_cones(WORLDS/'competition/amz_fsi.csv')
    data = imported_reference(WORLDS/'amz_fsi.sdf')
    with pytest.raises(ValueError, match='spawn does not fit'):
        checked_spawn(cones, data, data['alignment_spawn'])
    assert checked_spawn(cones, data, data['alignment_spawn'], fit=True) == data['spawn']
    with pytest.raises(ValueError, match='within 5 m'):
        checked_spawn(cones, data, [10000., 10000., 0.], fit=True)
