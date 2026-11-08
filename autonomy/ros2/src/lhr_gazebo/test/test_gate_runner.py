"""Exercise gate planning and provenance without starting or killing ROS processes."""

import os
from pathlib import Path
import subprocess

import pytest


TOOLS = Path(__file__).parents[4] / 'testing/tools/gate'


def runner_prefix():
    """Load the runner's functions without executing its campaign or cleanup."""
    script = (TOOLS / 'run_gate.sh').read_text().split('# Skidpad joins the matrix')[0]
    return script.replace('HERE=$(cd "$(dirname "$0")" && pwd)', f'HERE="{TOOLS}"')


@pytest.mark.parametrize('style', ['autocross', 'rules_autocross'])
@pytest.mark.parametrize('baseline', ['0', '1'])
def test_gate_plan_selects_fusion_without_changing_truth_baselines(tmp_path, style, baseline):
    """Every event uses the gate sensors; truth baselines remain launchable."""
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
    for run_id, args in rows.items():
        if run_id.startswith('baseline_'):
            assert args['perception'] == 'sim'
            assert args['estimator'] == 'truth'
            assert args.get('lidar_source', 'gazebo') == 'gazebo'
            assert args.get('enable_stereo_camera', 'false') == 'false'
            assert args.get('enable_camera_lidar_fusion', 'false') == 'false'
        else:
            assert args['perception'] == 'lidar'
            assert args['estimator'] == 'ekf'
            assert args.get('lidar_source') == 'mid360'
            assert args.get('enable_stereo_camera') == 'true'
            assert args.get('enable_camera_lidar_fusion') == 'true'
    assert rows['skidpad_r1']['mission'] == 'skidpad'


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
one_run "$RUN_ID" track_style:="$1" seed:=9 perception:=lidar estimator:=ekf \
    lidar_source:=mid360 enable_stereo_camera:=true enable_camera_lidar_fusion:=true
one_run "$RUN_ID" track_style:="$1" seed:=9
"""
    subprocess.run(['bash', '-c', runner_prefix() + stubs, 'test', style],
                   env=env, text=True, capture_output=True, check=True)
    saved = (out / 'runs' / f'{style}_s9_r1' / 'args.txt').read_text().splitlines()
    assert saved == (out / 'executed.txt').read_text().splitlines()
    assert saved[:4] == ['ros2', 'launch', 'lhr_gazebo', 'gazebo_demo.launch.py']
    assert {'gui:=false', 'rviz:=false', 'safety:=true',
            f'output_csv:={out}/runs/{style}_s9_r1/metrics.csv'} <= set(saved)
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
