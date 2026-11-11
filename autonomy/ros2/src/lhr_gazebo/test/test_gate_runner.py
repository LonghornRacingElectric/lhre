"""Exercise gate planning and provenance without starting or killing ROS processes."""

import os
from pathlib import Path
import subprocess

import pytest


TOOLS = Path(__file__).parents[4] / 'testing/tools/gate'
FAULT_TOOLS = TOOLS.parent / 'fault_injection'
HERE_LINE = 'HERE=$(cd "$(dirname "$0")" && pwd)'


def runner_prefix():
    """Load the runner's functions without executing its campaign or cleanup."""
    script = (TOOLS / 'run_gate.sh').read_text()
    # Without the marker the whole campaign would run, cleanup included.
    assert '# Skidpad joins the matrix' in script
    return script.split('# Skidpad joins the matrix')[0].replace(HERE_LINE, f'HERE="{TOOLS}"')


def fault_prefix():
    """Load the fault runner's functions without running its campaign."""
    script = (FAULT_TOOLS / 'run_faults.sh').read_text()
    assert '# The campaign.' in script
    return script.split('# The campaign.')[0].replace(HERE_LINE, f'HERE="{FAULT_TOOLS}"')


def gate_stack():
    """Return stack.args as the runners read it, through their own helper."""
    line = subprocess.run(['bash', '-c', f'source "{TOOLS}/stack.sh"; gate_stack'],
                          text=True, capture_output=True, check=True).stdout.split()
    return dict(arg.split(':=', 1) for arg in line)


def test_gate_stack_is_the_sim_v2_stack():
    """The file is the one place the gated stack is written down (#237)."""
    assert gate_stack() == {
        'perception': 'lidar', 'estimator': 'ekf', 'safety': 'true',
        'lidar_source': 'mid360', 'enable_stereo_camera': 'true',
        'enable_camera_lidar_fusion': 'true'}


@pytest.mark.parametrize('content', ['', '# only a comment\n', 'perception:=lidar\nnot one\n',
                                     'fault:=a b\n', 'a:=b:=c\n'])
@pytest.mark.parametrize('runner', ['gate', 'faults'])
def test_runners_refuse_an_unreadable_stack(tmp_path, content, runner):
    """A broken stack file stops the runner before any run is planned."""
    stack = tmp_path / 'stack.args'
    stack.write_text(content)
    prefix = runner_prefix() if runner == 'gate' else fault_prefix()
    env = dict(os.environ, GATE_STACK_FILE=str(stack), OUT=str(tmp_path))
    result = subprocess.run(['bash', '-c', prefix + '\necho planned'],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert 'planned' not in result.stdout
    assert 'gate stack' in result.stderr


@pytest.mark.parametrize('style', ['autocross', 'rules_autocross'])
@pytest.mark.parametrize('baseline', ['0', '1'])
def test_gate_plan_selects_fusion_without_changing_truth_baselines(tmp_path, style, baseline):
    """Every event runs the whole gate stack; only truth baselines opt out."""
    world = tmp_path / 'skidpad.sdf'
    world.touch()
    env = dict(os.environ, TRACK_STYLE=style, SEEDS='1 2', REPEATS='2',
               ACCEL_REPEATS='1', SKIDPAD_REPEATS='1', BASELINE=baseline, OUT=str(tmp_path))
    result = subprocess.run(
        ['bash', '-c', runner_prefix() + '\nSKIDPAD_WORLD="$1"\nplan', 'test', str(world)],
        env=env, text=True, capture_output=True, check=True)
    rows = {line.split()[0]: dict(arg.split(':=', 1) for arg in line.split()[1:])
            for line in result.stdout.splitlines()}
    expected = {f'{style}_s{s}_r{r}' for s in (1, 2) for r in (1, 2)}
    expected |= {'acceleration_r1', 'skidpad_r1'}
    if baseline == '1':
        expected |= {f'baseline_{style}_s{s}' for s in (1, 2)}
    assert set(rows) == expected
    stack = gate_stack()
    for run_id, args in rows.items():
        if run_id.startswith('baseline_'):
            assert args['perception'] == 'sim'
            assert args['estimator'] == 'truth'
            assert args['safety'] == 'true'
            assert args.get('lidar_source', 'gazebo') == 'gazebo'
            assert args.get('enable_stereo_camera', 'false') == 'false'
            assert args.get('enable_camera_lidar_fusion', 'false') == 'false'
        else:
            assert stack.items() <= args.items(), f'{run_id} leaves out part of the gate stack'
    assert rows['skidpad_r1']['mission'] == 'skidpad'


def test_fault_plan_runs_every_case_and_clean_lap_on_the_gate_stack(tmp_path):
    """The fault matrix gates on the same stack as the gate (#237)."""
    cases = ('ekf_kill controller_kill controller_pause odom_nan odom_spike lidar_drop '
             'cmd_nan steer_freeze geofence')
    env = dict(os.environ, CASES=cases, SPEEDS='3 8', REPEATS='2', CLEAN_SEEDS='1 2',
               OUT=str(tmp_path))
    result = subprocess.run(['bash', '-c', fault_prefix() + '\nplan'],
                            env=env, text=True, capture_output=True, check=True)
    rows = {}
    for line in result.stdout.splitlines():
        run_id, kind, track, seed, v_max, secs, *launch = line.split()
        rows[run_id] = (kind, track, seed, v_max, secs, dict(a.split(':=', 1) for a in launch))
    expected = {f'{kind}_v{v}_r{r}' for kind in cases.split() for v in (3, 8) for r in (1, 2)}
    expected |= {'clean_s1', 'clean_s2'}
    assert set(rows) == expected
    stack = gate_stack()
    for run_id, (kind, track, seed, v_max, secs, args) in rows.items():
        assert stack.items() <= args.items(), f'{run_id} leaves out part of the gate stack'
        if kind == 'clean':
            assert (track, v_max) == ('autocross', '5') and 'fault' not in args
        elif kind == 'geofence':
            assert args['safety_geofence_radius_m'] == '20' and 'fault' not in args
        else:
            assert (track, seed) == ('oval', '1')
            assert args['fault'] == kind and args['fault_after_s'] == '25'
    assert rows['controller_pause_v3_r1'][5]['fault_duration_s'] == '3'


@pytest.mark.parametrize('style', ['autocross', 'rules_autocross'])
@pytest.mark.parametrize('extra', ['', 'fusion_camera_max_age_sec:=0.9'])
def test_completed_run_records_executed_arguments_and_resumes(tmp_path, style, extra):
    """Provenance includes common/world/extra arguments and is written after launch."""
    out = tmp_path / 'campaign with spaces'
    env = dict(os.environ, OUT=str(out), EXTRA=extra,
               RUN_S='12', SETTLE_S='5', HANG_S='30')
    # Replace only external commands. Keep one_run(), its background jobs and
    # completion/resume logic real; never let this test invoke process cleanup.
    stubs = r"""
cleanup() { :; }
pkill() { :; }
python3() {
    case "$1" in
        */generate_world.py) touch "$OUT/worlds/rules_autocross_seed9.sdf" ;;
        */watch_run.py)
            printf '%s\n' "$@" > "$OUT/watcher.txt"
            for _ in $(seq 100); do
                [ -e "$OUT/executed.txt" ] && break
                sleep .01
            done
            [ ! -e "$OUT/runs/$RUN_ID/args.txt" ] || touch "$OUT/early-marker"
            ;;
    esac
}
timeout() { return 97; }
ros2() {
    if [ "$1" = launch ]; then
        printf '%s\n' ros2 "$@" > "$OUT/executed.txt"
        echo launch >> "$OUT/launches.txt"
        [ ! -e "$OUT/runs/$RUN_ID/args.txt" ] || touch "$OUT/early-marker"
    fi
}
RUN_ID="${1}_s9_r1"
one_run "$RUN_ID" track_style:="$1" seed:=9 $STACK
one_run "$RUN_ID" track_style:="$1" seed:=9
"""
    subprocess.run(['bash', '-c', runner_prefix() + stubs, 'test', style],
                   env=env, text=True, capture_output=True, check=True)
    saved = (out / 'runs' / f'{style}_s9_r1' / 'args.txt').read_text().splitlines()
    assert saved == (out / 'executed.txt').read_text().splitlines()
    assert saved[:4] == ['ros2', 'launch', 'lhr_gazebo', 'gazebo_demo.launch.py']
    assert {'gui:=false', 'rviz:=false',
            f'output_csv:={out}/runs/{style}_s9_r1/metrics.csv'} <= set(saved)
    assert {f'{name}:={value}' for name, value in gate_stack().items()} <= set(saved)
    if extra:
        assert extra in saved
    worlds = [arg for arg in saved if arg.startswith('world:=')]
    assert worlds == ([f'world:={out}/worlds/rules_autocross_seed9.sdf']
                      if style == 'rules_autocross' else [])
    assert not (out / 'early-marker').exists()
    assert (out / 'launches.txt').read_text().splitlines() == ['launch']
    watcher = (out / 'watcher.txt').read_text().splitlines()
    assert watcher[0] == str(TOOLS / 'watch_run.py')
    options = dict(zip(watcher[1::2], watcher[2::2]))
    assert options['--cap'] == '12'
    assert options['--settle'] == '5'
    assert options['--hang'] == '30'
    assert int(options['--pid']) > 0
    assert options['--out'] == str(out / 'runs' / f'{style}_s9_r1' / 'end.txt')
