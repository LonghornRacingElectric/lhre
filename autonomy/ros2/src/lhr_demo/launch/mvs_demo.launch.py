"""Launch the full MVS autonomy demo stack."""

import math
from pathlib import Path
import subprocess
import time

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, EmitEvent, ExecuteProcess, OpaqueFunction, RegisterEventHandler)
from launch.conditions import IfCondition, UnlessCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from lhr_trackgen.publish_cones import GENERATORS
import numpy as np


# Recorded when `record:=true`. An explicit list rather than --all: the
# viz topics republish every cone marker at rate and would dominate the
# bag, and this list doubles as a statement of what the seam between
# lanes actually is. A topic nothing publishes in a given mode costs
# nothing, so `/lhr/lidar/points` stays in for the Gazebo and on-car
# paths that do publish it.
RECORDED_TOPICS = (
    # Without /clock a sim-time bag has no timeline a viewer can read.
    '/clock',
    '/lhr/sim/provenance',
    '/lhr/scene/clutter',
    '/tf',
    '/tf_static',
    # The perception contract: points in, detected cones out, with the
    # ground truth alongside so a bag can be scored and not just watched.
    '/lhr/lidar/points',
    '/lhr/sensor/cones_detected',
    '/lhr/track/cones',
    # What the car looks like and where its sensor sits, so a bag
    # opens as a car on a track rather than a cloud in a void. Both are
    # latched and published once, so they cost a bag almost nothing.
    '/lhr/vehicle/body',
    '/lhr/lidar/sensor',
    # What the rest of the stack did with them.
    '/lhr/track/centerline',
    '/lhr/vehicle/odom',
    '/lhr/vehicle/cmd',
    '/lhr/mission/status',
    '/lhr/metrics/lap_complete',
    '/lhr/imu/data',
)

# Launch arguments left out of a bag's provenance: they decide where
# output lands and who is watching, not what the car did. Everything
# else is recorded, because an argument that shapes a run and goes
# unrecorded makes the bag unreproducible, and the one that did it was
# the Mid-360 mount pitch, which changes every point in the cloud.
UNRECORDED_ARGS = frozenset({
    'bag_dir',
    'enable_metrics',
    'foxglove',
    'foxglove_port',
    'output_csv',
    'record',
    'run_id',  # recorded by hand, ahead of the rest
})


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


def _typed(name: str, kind):
    """
    Return a launch argument as a parameter of a definite type.

    Launch hands parameters to a node through a YAML file, so a value
    arrives as whatever YAML decides it is. `v_max:=10` becomes the
    integer 10, a node declaring a double rejects it, and the run dies
    on a traceback for want of a decimal point. Declaring the type here
    makes launch do the conversion, so round numbers work.
    """
    return ParameterValue(LaunchConfiguration(name), value_type=kind)


def _f(name: str):
    """Return a launch argument as a float parameter."""
    return _typed(name, float)


def _i(name: str):
    """Return a launch argument as an int parameter."""
    return _typed(name, int)


def _s(name: str):
    """Return a launch argument as a string parameter."""
    return _typed(name, str)


def _b(name: str):
    """Return a launch argument as a bool parameter."""
    return _typed(name, bool)


def _launch_sim(context):
    """Spawn the LiDAR-mode car on the generated track, respecting manual mode."""
    def value(name):
        return LaunchConfiguration(name).perform(context)

    x, y, yaw = (float(value(name)) for name in ('init_x', 'init_y', 'init_yaw'))
    if value('perception') == 'lidar' and value('start_on_track') == 'true':
        left, right = GENERATORS.get(value('track_style'), GENERATORS['autocross'])(
            seed=int(value('seed')), num_waypoints=int(value('num_waypoints')),
            radius_m=25.0, jitter_m=10.0, width_m=3.5, cone_spacing_m=2.0)
        centers = (np.asarray(left) + np.asarray(right)) / 2.0
        index = int(np.argmin(np.linalg.norm(centers - [x, y], axis=1)))
        x, y = map(float, centers[index])
        tangent = centers[(index + 1) % len(centers)] - centers[index - 1]
        yaw = math.atan2(float(tangent[1]), float(tangent[0]))
    plant = value('plant')
    if plant not in ('kinematic', 'bobsim'):
        raise ValueError('plant must be kinematic or bobsim')
    extra = ({'max_accel': float(value('max_accel')),
              'max_decel': float(value('max_decel'))} if plant == 'bobsim' else {})
    simulator = Node(
        package='lhr_sim_' + plant, executable='sim_node', name='sim_' + plant,
        parameters=[{'publish_clock': value('use_sim_time') == 'true',
                     'init_x': x, 'init_y': y, 'init_yaw': yaw, **extra}],
        output='screen')
    return [simulator, RegisterEventHandler(OnProcessExit(
        target_action=simulator,
        on_exit=[EmitEvent(event=Shutdown(reason='Vehicle plant exited'))]))]


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
    v_max_arg = DeclareLaunchArgument(
        'v_max', default_value=PythonExpression([
            "'4.0' if '", LaunchConfiguration('perception'),
            "' == 'lidar' else '12.0'"]),
        description='Speed ceiling: 4 m/s for LiDAR preview, 12 m/s for sim')
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
    start_on_track_arg = DeclareLaunchArgument(
        'start_on_track', default_value='true', choices=['true', 'false'],
        description='In LiDAR mode, spawn at the nearest generated-track '
                    'center and tangent; false uses the exact init pose')

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

    # A mesh marker names an asset the viewer has to fetch, which the
    # live bridge serves and a bag cannot. Recorded in provenance
    # because it decides what the bag's /lhr/vehicle/body actually is.
    vehicle_mesh_arg = DeclareLaunchArgument(
        'vehicle_mesh', default_value='true',
        description="Draw Orion's CAD mesh. false falls back to the box "
                    'and cylinders, which need no asset server and so '
                    'still render when replaying a bag')

    perception_arg = DeclareLaunchArgument(
        'perception', default_value='sim', choices=['sim', 'lidar'],
        description='Cone source: simplified sensor sim or LiDAR detector')

    # Synthetic Mid-360. Off by default: the cheat-mode sensor sim is
    # what the gate's numbers were measured against, and swapping the
    # perception front end silently would make those numbers lie.
    lidar_arg = DeclareLaunchArgument(
        'lidar', default_value='false',
        description='Publish a synthetic Livox Mid-360 cloud on '
                    '/lhr/lidar/points alongside the stack')
    lidar_pitch_arg = DeclareLaunchArgument(
        'mount_pitch_rad', default_value='0.0',
        description='Mid-360 mount pitch, positive is nose down. The '
                    'mount study has not settled this yet')
    return_profile_arg = DeclareLaunchArgument(
        'return_profile', default_value='baseline',
        choices=['baseline', 'acceptance_overcast'],
        description='Small-cone return density: baseline or recorded overcast acceptance fit')
    lidar_profile_arg = DeclareLaunchArgument(
        'elevation_profile', default_value='livox',
        description="Beam pattern: 'livox' vendor table (default), "
                    "or legacy synthetic 'rosette'/'uniform' studies")

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

    gates_arg = DeclareLaunchArgument(
        'start_finish_cones', default_value='false',
        description='Add nominal large orange start/finish gate cones')
    stack_arg = DeclareLaunchArgument(
        'stack_window_sec', default_value='0.5',
        description='Scan-time compensated cloud history in seconds; 0 disables stacking')
    cluster_arg = DeclareLaunchArgument(
        'min_cluster_points', default_value='3',
        description='Independent points required in a stacked cone cluster')
    ground_arg = DeclareLaunchArgument(
        'ground_z_min', default_value='0.05',
        description='Flat-ground height cutoff in base_link, metres')

    # Declared and recorded off the same list on purpose. Keeping two
    # lists meant a new argument reached the nodes but never reached the
    # bag, so the recording claimed to describe a run it could not.
    launch_args = [
        DeclareLaunchArgument('motion_distortion', default_value='false'),
        DeclareLaunchArgument('clutter_profile', default_value='none',
                              choices=['none', 'trackside']),
        DeclareLaunchArgument(
            'plant', default_value='kinematic', choices=['kinematic', 'bobsim']),
        perception_arg,
        gates_arg,
        stack_arg,
        cluster_arg,
        ground_arg,
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
        start_on_track_arg,
        mission_arg,
        auto_go_arg,
        ready_hold_arg,
        run_id_arg,
        record_arg,
        bag_dir_arg,
        foxglove_arg,
        foxglove_port_arg,
        vehicle_mesh_arg,
        lidar_arg,
        lidar_pitch_arg,
        lidar_profile_arg,
        return_profile_arg,
    ]

    # ----- Nodes -----
    vehicle_viz = Node(
        package='lhr_vehicle',
        executable='vehicle_viz',
        name='vehicle_viz',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'use_mesh': _b('vehicle_mesh'),
        }],
        output='screen',
    )

    cones = Node(
        package='lhr_trackgen',
        executable='publish_cones',
        name='publish_cones',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'seed': _i('seed'),
            'start_finish_cones': _b('start_finish_cones'),
            'track_style': _s('track_style'),
            'num_waypoints': _i('num_waypoints'),
        }],
        output='screen',
    )

    lidar_perception = PythonExpression([
        "'", LaunchConfiguration('perception'), "' == 'lidar'"])

    lidar_enabled = PythonExpression([
        "'", LaunchConfiguration('lidar'), "'.lower() == 'true' or '",
        LaunchConfiguration('perception'), "' == 'lidar'"])

    sensor_sim = Node(
        package='lhr_sensor_sim',
        executable='sensor_sim',
        name='sensor_sim',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'fov_deg': _f('fov_deg'),
            'max_range_m': _f('max_range_m'),
            'noise_std_m': _f('noise_std_m'),
            'false_negative_rate': _f('false_negative_rate'),
            'seed': _i('seed'),
        }],
        output='screen',
        condition=UnlessCondition(lidar_perception),
    )

    centerline = Node(
        package='lhr_track_builder',
        executable='track_builder',
        name='track_builder',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'pairing_strategy': ParameterValue(PythonExpression([
                "'boundary' if '", LaunchConfiguration('perception'),
                "' == 'lidar' else 'index'"]), value_type=str),
        }],
        output='screen',
    )

    sim = OpaqueFunction(function=_launch_sim)

    control = Node(
        package='lhr_control',
        executable='pursuit_node',
        name='pure_pursuit',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'closed_path': ParameterValue(PythonExpression([
                "'", LaunchConfiguration('perception'), "' != 'lidar'"]), value_type=bool),
            'lookahead_dist': _f('lookahead_dist'),
            'a_lat_max': _f('a_lat_max'),
            'v_min': _f('v_min'),
            'v_max': _f('v_max'),
            'max_accel': _f('max_accel'),
            'max_decel': _f('max_decel'),
        }],
        output='screen',
    )

    metrics = Node(
        package='lhr_metrics',
        executable='metrics_node',
        name='metrics_node',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'timeout_sec': _f('timeout_sec'),
            'output_csv': _s('output_csv'),
            'run_id': _s('run_id'),
            'scenario': _s('scenario'),
            'git_sha': _git_sha(),
            'seed': _i('seed'),
            'track_style': _s('track_style'),
            'num_waypoints': _i('num_waypoints'),
            'mission': _s('mission'),
            'perception': _s('perception'),
            'start_finish_cones': _b('start_finish_cones'),
            'stack_window_sec': _f('stack_window_sec'),
            'min_cluster_points': _i('min_cluster_points'),
            'ground_z_min': _f('ground_z_min'),
            'start_on_track': _b('start_on_track'),
            'lidar': ParameterValue(lidar_enabled, value_type=bool),
            'mount_pitch_rad': _f('mount_pitch_rad'),
            'elevation_profile': _s('elevation_profile'),
            'return_profile': _s('return_profile'),
            'motion_distortion': _b('motion_distortion'),
            'clutter_profile': _s('clutter_profile'),
            'plant': _s('plant'),
            'fov_deg': _f('fov_deg'),
            'max_range_m': _f('max_range_m'),
            'noise_std_m': _f('noise_std_m'),
            'false_negative_rate': _f('false_negative_rate'),
            'lookahead_dist': _f('lookahead_dist'),
            'a_lat_max': _f('a_lat_max'),
            'v_min': _f('v_min'),
            'v_max': _f('v_max'),
            'max_accel': _f('max_accel'),
            'max_decel': _f('max_decel'),
        }],
        output='screen',
        condition=IfCondition(LaunchConfiguration('enable_metrics')),
    )

    mission_mgr = Node(
        package='lhr_mission_manager',
        executable='mission_manager',
        name='mission_manager',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'mission': _s('mission'),
            'auto_go': _b('auto_go'),
            'ready_hold_sec': _f('ready_hold_sec'),
        }],
        output='screen',
    )

    lidar_sim = Node(
        package='lhr_lidar_sim',
        executable='lidar_sim',
        name='lidar_sim',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'seed': _i('seed'),
            'mount_pitch_rad': _f('mount_pitch_rad'),
            'elevation_profile': _s('elevation_profile'),
            'return_profile': _s('return_profile'),
            'motion_distortion': _b('motion_distortion'),
            'clutter_profile': _s('clutter_profile'),
        }],
        output='screen',
        condition=IfCondition(lidar_enabled),
    )

    detector = Node(
        package='lhr_perception',
        executable='lidar_cone_detector',
        name='lidar_cone_detector',
        parameters=[{
            'use_sim_time': _b('use_sim_time'),
            'max_range': _f('max_range_m'),
            'stack_window_sec': _f('stack_window_sec'),
            'min_cluster_points': _i('min_cluster_points'),
            'ground_z_min': _f('ground_z_min'),
        }],
        output='screen',
        condition=IfCondition(lidar_perception),
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
            # The bag carries the same provenance as the metrics row and
            # every argument the run resolved, so a recording found later
            # says what produced it without anyone reading it back out of
            # the data.
            '--custom-data',
            ['run_id=', LaunchConfiguration('run_id')],
            f'git_sha={_git_sha()}',
            *[[f'{arg.name}=', LaunchConfiguration(arg.name)]
              for arg in launch_args
              if arg.name not in UNRECORDED_ARGS],
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
            'use_sim_time': _b('use_sim_time'),
            'port': _i('foxglove_port'),
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
        *launch_args,
        vehicle_viz,
        cones,
        sensor_sim,
        centerline,
        sim,
        mission_mgr,
        control,
        metrics,
        lidar_sim,
        detector,
        recorder(sim_time=True),
        recorder(sim_time=False),
        foxglove_bridge,
        stop_when_metrics_exits,
    ])
