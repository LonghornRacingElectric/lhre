"""Launch the full MVS autonomy demo stack."""

from pathlib import Path
import subprocess
import time

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, EmitEvent, ExecuteProcess, RegisterEventHandler)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


# Recorded when `record:=true`. An explicit list rather than --all: the
# viz topics republish every cone marker at rate and would dominate the
# bag, and this list doubles as a statement of what the seam between
# lanes actually is. A topic nothing publishes in a given mode costs
# nothing, so `/lhr/lidar/points` stays in for the Gazebo and on-car
# paths that do publish it.
RECORDED_TOPICS = (
    # Without /clock a sim-time bag has no timeline a viewer can read.
    '/clock',
    '/tf',
    '/tf_static',
    # The perception contract: points in, detected cones out, with the
    # ground truth alongside so a bag can be scored and not just watched.
    '/lhr/lidar/points',
    '/lhr/sensor/cones_detected',
    '/lhr/track/cones',
    # What the rest of the stack did with them.
    '/lhr/track/centerline',
    '/lhr/vehicle/odom',
    '/lhr/vehicle/cmd',
    '/lhr/mission/status',
    '/lhr/metrics/lap_complete',
    '/lhr/imu/data',
)


def _git_sha() -> str:
    """
    Short commit id of the tree this launch file came from, or 'unknown'.

    Recorded on every metrics row so an old result can be traced back to
    the code that produced it. Resolved from this file's own path rather
    than the working directory, because a run started from elsewhere
    would otherwise record 'unknown' or, worse, an unrelated
    repository's commit. build.sh passes --symlink-install, so the
    installed copy resolves back into the checkout.

    A '-dirty' suffix marks uncommitted changes anywhere in the repo,
    since a clean-looking id on a modified tree is worse than no id.
    """
    here = str(Path(__file__).resolve().parent)

    def git(*args: str) -> str:
        return subprocess.check_output(
            ('git', '-C', here) + args,
            stderr=subprocess.DEVNULL, text=True).strip()

    try:
        sha = git('rev-parse', '--short', 'HEAD')
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'
    try:
        dirty = bool(git('status', '--porcelain'))
    except (OSError, subprocess.CalledProcessError):
        dirty = False
    return f'{sha}-dirty' if dirty else sha


def generate_launch_description():
    # ----- Launch arguments -----
    seed_arg = DeclareLaunchArgument('seed', default_value='1')
    lookahead_arg = DeclareLaunchArgument(
        'lookahead_dist', default_value='4.0')
    metrics_arg = DeclareLaunchArgument(
        'enable_metrics', default_value='true',
        description='Launch metrics node alongside the stack')
    sim_time_arg = DeclareLaunchArgument(
        'use_sim_time', default_value='true',
        description='Drive every node off the simulator clock. Off '
                    'means wall time, and averaged metrics wander')
    timeout_arg = DeclareLaunchArgument(
        'timeout_sec', default_value='120.0',
        description='Wall-clock watchdog. Ends the run non-zero rather '
                    'than hanging when the sim stops')
    csv_arg = DeclareLaunchArgument(
        'output_csv', default_value='data/metrics.csv',
        description='Where the metrics row goes, relative to the '
                    'working directory. A runner points this at its '
                    'own output directory')
    scenario_arg = DeclareLaunchArgument(
        'scenario', default_value='mvs_demo',
        description='Name recorded on the metrics row, so runs from '
                    'different presets can be told apart')
    track_style_arg = DeclareLaunchArgument(
        'track_style', default_value='autocross',
        description='Track generator: autocross | simple')
    num_wp_arg = DeclareLaunchArgument(
        'num_waypoints', default_value='10')

    # Speed planning
    a_lat_arg = DeclareLaunchArgument('a_lat_max', default_value='6.0')
    v_min_arg = DeclareLaunchArgument('v_min', default_value='2.0')
    v_max_arg = DeclareLaunchArgument('v_max', default_value='12.0')
    max_accel_arg = DeclareLaunchArgument('max_accel', default_value='2.0')
    max_decel_arg = DeclareLaunchArgument('max_decel', default_value='3.0')

    # Sensor sim
    fov_arg = DeclareLaunchArgument('fov_deg', default_value='200.0')
    range_arg = DeclareLaunchArgument('max_range_m', default_value='20.0')
    noise_arg = DeclareLaunchArgument(
        'noise_std_m', default_value='0.0',
        description='Cone position noise, metres std dev. Seeded, so a '
                    'given seed repeats')
    fn_arg = DeclareLaunchArgument(
        'false_negative_rate', default_value='0.0',
        description='Chance a visible cone is missed, 0.0 to 1.0')

    # Initial pose
    init_x_arg = DeclareLaunchArgument('init_x', default_value='25.0')
    init_y_arg = DeclareLaunchArgument('init_y', default_value='0.0')
    init_yaw_arg = DeclareLaunchArgument('init_yaw', default_value='1.5708')

    # Recording and viewing
    # The default run id is shared by the metrics row and the bag
    # directory, so a row that reports a bad number names the recording
    # that explains it.
    # 'T' and not '_' between date and time on purpose: launch passes
    # parameters through a YAML file, and YAML reads 20261004_120000 as
    # the integer 20261004120000 because underscores are digit
    # separators. That silently renamed the run and left the bag
    # directory and the metrics row disagreeing.
    run_id_arg = DeclareLaunchArgument(
        'run_id', default_value=time.strftime('%Y%m%dT%H%M%S'),
        description='Identifier shared by the metrics row and the bag '
                    'directory, so the two can be matched up later')
    record_arg = DeclareLaunchArgument(
        'record', default_value='false',
        description='Record an MCAP bag of the contract topics. Off by '
                    'default so repeated gate runs do not fill the disk')
    bag_dir_arg = DeclareLaunchArgument(
        'bag_dir', default_value='data/bags',
        description='Parent directory for bags, relative to the working '
                    'directory')
    foxglove_arg = DeclareLaunchArgument(
        'foxglove', default_value='false',
        description='Serve the live graph to Foxglove over websocket. '
                    'Needs ros-jazzy-foxglove-bridge installed')
    foxglove_port_arg = DeclareLaunchArgument(
        'foxglove_port', default_value='8765')

    # Mission manager
    mission_arg = DeclareLaunchArgument(
        'mission', default_value='autocross',
        description='Mission: inspection | manual | ebs_test '
                    '| acceleration | skidpad | autocross')
    auto_go_arg = DeclareLaunchArgument(
        'auto_go', default_value='true',
        description='Auto-transition READY -> DRIVING after hold time')
    ready_hold_arg = DeclareLaunchArgument(
        'ready_hold_sec', default_value='5.0',
        description='Seconds to hold in READY before auto-go')

    # ----- Nodes -----
    cones = Node(
        package='lhr_trackgen',
        executable='publish_cones',
        name='publish_cones',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'seed': LaunchConfiguration('seed'),
            'track_style': LaunchConfiguration('track_style'),
            'num_waypoints': LaunchConfiguration('num_waypoints'),
        }],
        output='screen',
    )

    sensor_sim = Node(
        package='lhr_sensor_sim',
        executable='sensor_sim',
        name='sensor_sim',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'fov_deg': LaunchConfiguration('fov_deg'),
            'max_range_m': LaunchConfiguration('max_range_m'),
            'noise_std_m': LaunchConfiguration('noise_std_m'),
            'false_negative_rate': LaunchConfiguration(
                'false_negative_rate'),
            'seed': LaunchConfiguration('seed'),
        }],
        output='screen',
    )

    centerline = Node(
        package='lhr_track_builder',
        executable='track_builder',
        name='track_builder',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
        }],
        output='screen',
    )

    sim = Node(
        package='lhr_sim_kinematic',
        executable='sim_node',
        name='sim_kinematic',
        parameters=[{
            'publish_clock': LaunchConfiguration('use_sim_time'),
            'init_x': LaunchConfiguration('init_x'),
            'init_y': LaunchConfiguration('init_y'),
            'init_yaw': LaunchConfiguration('init_yaw'),
        }],
        output='screen',
    )

    control = Node(
        package='lhr_control',
        executable='pursuit_node',
        name='pure_pursuit',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'lookahead_dist': LaunchConfiguration('lookahead_dist'),
            'a_lat_max': LaunchConfiguration('a_lat_max'),
            'v_min': LaunchConfiguration('v_min'),
            'v_max': LaunchConfiguration('v_max'),
            'max_accel': LaunchConfiguration('max_accel'),
            'max_decel': LaunchConfiguration('max_decel'),
        }],
        output='screen',
    )

    metrics = Node(
        package='lhr_metrics',
        executable='metrics_node',
        name='metrics_node',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'timeout_sec': LaunchConfiguration('timeout_sec'),
            'output_csv': LaunchConfiguration('output_csv'),
            'run_id': LaunchConfiguration('run_id'),
            'scenario': LaunchConfiguration('scenario'),
            'git_sha': _git_sha(),
            'seed': LaunchConfiguration('seed'),
            'track_style': LaunchConfiguration('track_style'),
            'num_waypoints': LaunchConfiguration('num_waypoints'),
            'mission': LaunchConfiguration('mission'),
            'fov_deg': LaunchConfiguration('fov_deg'),
            'max_range_m': LaunchConfiguration('max_range_m'),
            'noise_std_m': LaunchConfiguration('noise_std_m'),
            'false_negative_rate': LaunchConfiguration(
                'false_negative_rate'),
            'lookahead_dist': LaunchConfiguration('lookahead_dist'),
            'a_lat_max': LaunchConfiguration('a_lat_max'),
            'v_min': LaunchConfiguration('v_min'),
            'v_max': LaunchConfiguration('v_max'),
            'max_accel': LaunchConfiguration('max_accel'),
            'max_decel': LaunchConfiguration('max_decel'),
        }],
        output='screen',
        condition=IfCondition(LaunchConfiguration('enable_metrics')),
    )

    mission_mgr = Node(
        package='lhr_mission_manager',
        executable='mission_manager',
        name='mission_manager',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'mission': LaunchConfiguration('mission'),
            'auto_go': LaunchConfiguration('auto_go'),
            'ready_hold_sec': LaunchConfiguration('ready_hold_sec'),
        }],
        output='screen',
    )

    def recorder(sim_time: bool) -> ExecuteProcess:
        """
        Build the bag recorder for one clock mode.

        Two actions rather than one, because --use-sim-time is a bare
        flag: it cannot be switched by a substitution, and passing it
        when nothing publishes /clock leaves the recorder waiting for a
        clock that never ticks.
        """
        cmd = [
            'ros2', 'bag', 'record',
            '--output', [LaunchConfiguration('bag_dir'), '/',
                         LaunchConfiguration('run_id')],
            '--storage', 'mcap',
            # The bag carries the same provenance as the metrics row, so
            # a recording found later still says what produced it.
            '--custom-data',
            ['run_id=', LaunchConfiguration('run_id')],
            f'git_sha={_git_sha()}',
            ['scenario=', LaunchConfiguration('scenario')],
            ['seed=', LaunchConfiguration('seed')],
        ]
        if sim_time:
            cmd.append('--use-sim-time')
        cmd += ['--topics', *RECORDED_TOPICS]
        return ExecuteProcess(
            cmd=cmd,
            output='screen',
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration('record'), "' == 'true' and '",
                LaunchConfiguration('use_sim_time'),
                "' == ", repr('true' if sim_time else 'false')])),
        )

    foxglove_bridge = Node(
        package='foxglove_bridge',
        executable='foxglove_bridge',
        name='foxglove_bridge',
        parameters=[{
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'port': LaunchConfiguration('foxglove_port'),
        }],
        output='screen',
        condition=IfCondition(LaunchConfiguration('foxglove')),
    )

    # A headless run has to end by itself. Metrics decides when the run
    # is over, so its exit tears the launch down and its exit code
    # becomes the run's verdict.
    stop_when_metrics_exits = RegisterEventHandler(
        OnProcessExit(
            target_action=metrics,
            on_exit=[EmitEvent(event=Shutdown())]))

    return LaunchDescription([
        seed_arg,
        lookahead_arg,
        metrics_arg,
        sim_time_arg,
        timeout_arg,
        csv_arg,
        scenario_arg,
        track_style_arg,
        num_wp_arg,
        a_lat_arg,
        v_min_arg,
        v_max_arg,
        max_accel_arg,
        max_decel_arg,
        fov_arg,
        range_arg,
        noise_arg,
        fn_arg,
        init_x_arg,
        init_y_arg,
        init_yaw_arg,
        mission_arg,
        auto_go_arg,
        ready_hold_arg,
        run_id_arg,
        record_arg,
        bag_dir_arg,
        foxglove_arg,
        foxglove_port_arg,
        cones,
        sensor_sim,
        centerline,
        sim,
        mission_mgr,
        control,
        metrics,
        recorder(sim_time=True),
        recorder(sim_time=False),
        foxglove_bridge,
        stop_when_metrics_exits,
    ])
