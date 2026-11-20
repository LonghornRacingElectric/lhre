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


@pytest.mark.parametrize('baseline', ['0', '1'])
@pytest.mark.parametrize('first', ['1', '101'])
def test_gate_draws_are_distinct_recorded_and_independent_of_track(tmp_path, baseline, first):
    env = dict(os.environ, SEEDS='7 9', REPEATS='2', ACCEL_REPEATS='3', SKIDPAD_REPEATS='1',
               BASELINE=baseline, IMU_SEED=first, OUT=str(tmp_path))
    world = tmp_path / 'skidpad.sdf'
    world.touch()
    command = ['bash', '-c', runner_prefix() + '\nSKIDPAD_WORLD="$1"\nplan', 'test', str(world)]
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
    assert result.stdout == subprocess.run(
        command, env=env, text=True, capture_output=True, check=True).stdout
    rows = {line.split()[0]: dict(arg.split(':=', 1) for arg in line.split()[1:])
            for line in result.stdout.splitlines() if not line.startswith('baseline_')}
    assert [int(args['imu_seed']) for args in rows.values()] == list(
        range(int(first), int(first) + 8))
    assert rows['autocross_s7_r1']['seed'] == rows['autocross_s7_r2']['seed'] == '7'
    assert rows['autocross_s9_r1']['seed'] == rows['autocross_s9_r2']['seed'] == '9'


@pytest.mark.parametrize('seed', ['-1', '4294967296', 'garbage'])
def test_gate_rejects_invalid_imu_seed_before_planning(tmp_path, seed):
    env = dict(os.environ, IMU_SEED=seed, OUT=str(tmp_path))
    result = subprocess.run(['bash', '-c', runner_prefix() + '\necho planned'],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert 'IMU_SEED' in result.stderr
    assert 'planned' not in result.stdout


def test_gate_seed_exhaustion_refuses_the_partial_plan(tmp_path):
    env = dict(os.environ, IMU_SEED='4294967295', SEEDS='1', REPEATS='2',
               ACCEL_REPEATS='0', SKIDPAD_REPEATS='0', BASELINE='0', OUT=str(tmp_path))
    result = subprocess.run(
        ['bash', '-c', runner_prefix() + '\nSKIDPAD_WORLD=/missing\nchecked_plan'],
        env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert result.stdout == ''
    assert 'plan failed' in result.stderr


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
            assert 'fault_min_speed_mps' not in args
        elif kind == 'geofence':
            assert args['safety_geofence_radius_m'] == '20' and 'fault' not in args
            assert 'fault_min_speed_mps' not in args
        else:
            assert (track, seed) == ('oval', '1')
            assert args['fault'] == kind and args['fault_after_s'] == '25'
            # Every injected fault waits for its tier's speed (#207).
            assert float(args['fault_min_speed_mps']) == pytest.approx(0.95 * float(v_max))
    assert rows['controller_pause_v3_r1'][5]['fault_duration_s'] == '3'


def test_default_fault_matrix_is_the_sim_v2_cases(tmp_path):
    """The Sim v2 criteria's fault cases; mission_kill has no stop path in sim yet (#127)."""
    env = dict(os.environ, SPEEDS='3', REPEATS='1', CLEAN_SEEDS='', OUT=str(tmp_path))
    env.pop('CASES', None)
    result = subprocess.run(['bash', '-c', fault_prefix() + '\nplan'],
                            env=env, text=True, capture_output=True, check=True)
    kinds = {line.split()[1] for line in result.stdout.splitlines()}
    assert kinds == {'ekf_kill', 'controller_kill', 'controller_pause', 'odom_nan',
                     'odom_spike', 'lidar_drop', 'cmd_nan', 'steer_freeze', 'imu_drop',
                     'wheel_drop', 'geofence'}


@pytest.mark.parametrize('share, gate', [('0.5', 4.0), ('1', 8.0), ('0', 0.0)])
def test_fault_plan_scales_the_speed_gate_with_at_speed(tmp_path, share, gate):
    env = dict(os.environ, CASES='ekf_kill', SPEEDS='8', REPEATS='1', CLEAN_SEEDS='',
               AT_SPEED=share, OUT=str(tmp_path))
    result = subprocess.run(['bash', '-c', fault_prefix() + '\nplan'],
                            env=env, text=True, capture_output=True, check=True)
    (line,) = result.stdout.splitlines()
    args = dict(a.split(':=', 1) for a in line.split()[6:])
    assert float(args['fault_min_speed_mps']) == pytest.approx(gate)


@pytest.mark.parametrize('share', ['0,95', 'abc', '1.5', '-0.1', '.'])
def test_fault_runner_refuses_a_bad_at_speed(tmp_path, share):
    """A typo must stop the runner, not silently turn the speed gate off."""
    env = dict(os.environ, AT_SPEED=share, OUT=str(tmp_path))
    result = subprocess.run(['bash', '-c', fault_prefix() + '\necho planned'],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert 'planned' not in result.stdout
    assert 'AT_SPEED' in result.stderr


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


# --- A plan that dies part way never runs half a campaign ---

RUNNERS = {
    'gate': (TOOLS / 'run_gate.sh', '# Skidpad joins the matrix', TOOLS),
    'faults': (FAULT_TOOLS / 'run_faults.sh', '# The campaign.', FAULT_TOOLS),
}
# Everything the campaign would start or kill, so no test touches a process.
CAMPAIGN_STUBS = r"""
pkill() { :; }
pgrep() { return 1; }
setsid() { :; }
ros2() { :; }
python3() { :; }
one_run() { echo "$1" >> "$OUT/ran.txt"; }
"""
# The runners' own settings: a stray one in the caller's environment must not
# change what a test plans.
RUNNER_VARS = ('SEEDS', 'REPEATS', 'ACCEL_REPEATS', 'SKIDPAD_REPEATS', 'BASELINE',
               'TRACK_STYLE', 'EXTRA', 'GATE_STACK_FILE', 'CASES', 'SPEEDS',
               'CLEAN_SEEDS', 'TRACK', 'AT_SPEED', 'STALE_NAME')


def runner_env(tmp_path, **settings):
    """Return a clean environment for one runner, writing only under tmp_path."""
    env = {k: v for k, v in os.environ.items() if k not in RUNNER_VARS}
    env.update(OUT=str(tmp_path), TMPDIR=str(tmp_path), **settings)
    return env


def campaign(runner, plan):
    """Return the whole runner with its processes stubbed and plan() replaced."""
    path, marker, here = RUNNERS[runner]
    script = path.read_text()
    assert marker in script
    head, tail = script.split(marker, 1)
    return head.replace(HERE_LINE, f'HERE="{here}"') + CAMPAIGN_STUBS + plan + marker + tail


@pytest.mark.parametrize('runner', ['gate', 'faults'])
@pytest.mark.parametrize('plan', [
    # set -u ends it at the stale name, after one row is out (#223, #218).
    'plan() { echo "first_r1 x:=1"; echo "$STALE_NAME"; echo "last_r1 x:=2"; }\n',
    # Inside a $(...) the stale name only kills that subshell, and plan()
    # carries on and returns 0 with the rows around it.
    'plan() { echo "first_r1 x:=1"; for r in $(seq 1 "$STALE_NAME"); do echo "r$r x:=2"; done;'
    ' echo "last_r1 x:=3"; }\n',
    'plan() { :; }\n',
])
def test_runners_refuse_a_plan_that_fails_or_is_empty(tmp_path, runner, plan):
    result = subprocess.run(['bash', '-c', campaign(runner, plan)],
                            env=runner_env(tmp_path), text=True, capture_output=True)
    assert result.returncode == 2
    assert 'plan failed or is empty' in result.stderr
    assert not (tmp_path / 'ran.txt').exists(), 'part of the plan ran'
    if runner == 'gate':
        assert not (tmp_path / 'expected.txt').exists()


@pytest.mark.parametrize('runner', ['gate', 'faults'])
def test_runners_run_every_planned_row_in_order(tmp_path, runner):
    plan = 'plan() { echo "first_r1 x:=1"; echo "second_r1 x:=2"; }\n'
    result = subprocess.run(['bash', '-c', campaign(runner, plan)],
                            env=runner_env(tmp_path), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'ran.txt').read_text().split() == ['first_r1', 'second_r1']
    if runner == 'gate':
        assert (tmp_path / 'expected.txt').read_text().split() == ['first_r1', 'second_r1']


@pytest.mark.parametrize('skidpad, rows', [(False, 18), (True, 21)])
def test_real_gate_plan_passes_the_check(tmp_path, skidpad, rows):
    """Skidpad's world is still missing on main: a complete plan without it is not refused."""
    world = tmp_path / 'skidpad.sdf'
    if skidpad:
        world.touch()
    result = subprocess.run(
        ['bash', '-c', runner_prefix() + '\nSKIDPAD_WORLD="$1"\nchecked_plan', 'test', str(world)],
        env=runner_env(tmp_path), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == rows


@pytest.mark.parametrize('repeats', ['3x', '-'])
def test_real_gate_plan_with_a_bad_repeat_count_is_refused(tmp_path, repeats):
    # seq fails inside a $(...): the autocross runs would have silently
    # dropped out and acceleration alone could score PASS.
    result = subprocess.run(
        ['bash', '-c', runner_prefix() + '\nSKIDPAD_WORLD=/nonexistent\nchecked_plan'],
        env=runner_env(tmp_path, REPEATS=repeats), text=True, capture_output=True)
    assert result.returncode == 2
    assert result.stdout == ''


def test_real_fault_plan_passes_the_check(tmp_path):
    result = subprocess.run(['bash', '-c', fault_prefix() + '\nchecked_plan'],
                            env=runner_env(tmp_path), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    # 11 cases at 3 speeds twice each, plus 5 clean laps.
    assert len(result.stdout.splitlines()) == 71
