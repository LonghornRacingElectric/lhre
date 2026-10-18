"""Launch the full MVS autonomy demo stack."""

from pathlib import Path
import subprocess
import time

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


RECORDED_TOPICS = (
    '/clock', '/tf', '/tf_static',
    '/lhr/lidar/points', '/lhr/sensor/cones_detected', '/lhr/track/cones',
    '/lhr/track/centerline', '/lhr/vehicle/odom',
    '/lhr/vehicle/odom_truth', '/lhr/vehicle/cmd',
    '/lhr/mission/status', '/lhr/metrics/lap_complete',
    '/lhr/imu/data', '/lhr/sensor/wheel_speeds',
)


def _git_sha() -> str:
    """Identify the source checkout used by this launch file."""
    repo = Path(__file__).resolve().parent
    try:
        sha = subprocess.check_output(
            ['git', '-C', str(repo), 'rev-parse', '--short', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).strip()
        status = subprocess.check_output(
            ['git', '-C', str(repo), 'status', '--porcelain'],
            text=True, stderr=subprocess.DEVNULL)
        return sha + ('-dirty' if status else '')
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def generate_launch_description():
    # ----- Launch arguments -----
    seed_arg = DeclareLaunchArgument('seed', default_value='1')
    lookahead_arg = DeclareLaunchArgument(
        'lookahead_dist', default_value='4.0')
    metrics_arg = DeclareLaunchArgument(
        'enable_metrics', default_value='true',
        description='Launch metrics node alongside the stack')
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

    # Initial pose
    init_x_arg = DeclareLaunchArgument('init_x', default_value='25.0')
    init_y_arg = DeclareLaunchArgument('init_y', default_value='0.0')
    init_yaw_arg = DeclareLaunchArgument('init_yaw', default_value='1.5708')

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

    # State estimation
    estimator_arg = DeclareLaunchArgument(
        'estimator', default_value='truth',
        description='Odometry source: "truth" (simulator) or "ekf"')
    gyro_bias_arg = DeclareLaunchArgument(
        'gyro_bias', default_value='0.0',
        description='Simulated gyro bias (rad/s); drives heading drift')
    wheel_scale_arg = DeclareLaunchArgument(
        'wheel_scale_error', default_value='1.0',
        description='Simulated rolling-radius error (1.0 = perfectly known)')

    # The row and bag share an ID; neither name depends on YAML's numeric
    # interpretation of an all-digit launch argument.
    run_id_arg = DeclareLaunchArgument(
        'run_id', default_value=time.strftime('%Y%m%dT%H%M%S'))
    output_csv_arg = DeclareLaunchArgument(
        'output_csv', default_value='data/metrics.csv')
    record_arg = DeclareLaunchArgument('record', default_value='false')
    bag_dir_arg = DeclareLaunchArgument('bag_dir', default_value='data/bags')
    foxglove_arg = DeclareLaunchArgument('foxglove', default_value='false')
    foxglove_port_arg = DeclareLaunchArgument(
        'foxglove_port', default_value='8765')
    lidar_arg = DeclareLaunchArgument(
        'lidar', default_value='false',
        description='Publish a synthetic Mid-360 cloud for inspection; '
                    'driving still uses the simplified cone sensor')
    mount_pitch_arg = DeclareLaunchArgument(
        'mount_pitch_rad', default_value='0.0')
    return_profile_arg = DeclareLaunchArgument(
        'return_profile', default_value='baseline',
        choices=['baseline', 'acceptance_overcast'])
    elevation_profile_arg = DeclareLaunchArgument(
        'elevation_profile', default_value='livox',
        choices=['livox', 'rosette', 'uniform'])
    motion_distortion_arg = DeclareLaunchArgument(
        'motion_distortion', default_value='false')
    clutter_profile_arg = DeclareLaunchArgument(
        'clutter_profile', default_value='none',
        choices=['none', 'trackside'])

    # ----- Nodes -----
    cones = Node(
        package='lhr_trackgen',
        executable='publish_cones',
        name='publish_cones',
        parameters=[{
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
            'fov_deg': LaunchConfiguration('fov_deg'),
            'max_range_m': LaunchConfiguration('max_range_m'),
        }],
        output='screen',
    )

    # Preview scans follow the physical simulator pose under EKF; the
    # estimator may drift, but it must not move the rendered scene.
    lidar_odom = PythonExpression([
        "'/lhr/vehicle/odom_truth' if '", LaunchConfiguration('estimator'),
        "' == 'ekf' else '/lhr/vehicle/odom'"])
    lidar_sim = Node(
        package='lhr_lidar_sim',
        executable='lidar_sim',
        name='lidar_sim',
        remappings=[('/lhr/vehicle/odom', lidar_odom)],
        parameters=[{
            'seed': ParameterValue(LaunchConfiguration('seed'), value_type=int),
            'mount_pitch_rad': ParameterValue(
                LaunchConfiguration('mount_pitch_rad'), value_type=float),
            'return_profile': ParameterValue(
                LaunchConfiguration('return_profile'), value_type=str),
            'elevation_profile': ParameterValue(
                LaunchConfiguration('elevation_profile'), value_type=str),
            'motion_distortion': ParameterValue(
                LaunchConfiguration('motion_distortion'), value_type=bool),
            'clutter_profile': ParameterValue(
                LaunchConfiguration('clutter_profile'), value_type=str),
        }],
        output='screen',
        condition=IfCondition(LaunchConfiguration('lidar')),
    )

    centerline = Node(
        package='lhr_track_builder',
        executable='track_builder',
        name='track_builder',
        output='screen',
    )

    # Odometry source A/B. Under 'ekf' the simulator's ground truth steps
    # aside to /lhr/vehicle/odom_truth (and /tf_truth) so the estimator owns
    # the topic and the transform the rest of the stack already consumes.
    use_ekf = PythonExpression(
        ["'", LaunchConfiguration('estimator'), "' == 'ekf'"])

    def sim_node(remappings, condition):
        return Node(
            package='lhr_sim_kinematic',
            executable='sim_node',
            name='sim_kinematic',
            parameters=[{
                'init_x': LaunchConfiguration('init_x'),
                'init_y': LaunchConfiguration('init_y'),
                'init_yaw': LaunchConfiguration('init_yaw'),
            }],
            remappings=remappings,
            condition=condition,
            output='screen',
        )

    sim_truth = sim_node([], UnlessCondition(use_ekf))
    sim_ekf = sim_node(
        [('/lhr/vehicle/odom', '/lhr/vehicle/odom_truth'),
         ('/tf', '/tf_truth')],
        IfCondition(use_ekf))

    inertial_sim = Node(
        package='lhr_sensor_sim',
        executable='inertial_sim',
        name='inertial_sim',
        parameters=[{
            'gyro_bias': LaunchConfiguration('gyro_bias'),
            'wheel_scale_error': LaunchConfiguration('wheel_scale_error'),
        }],
        condition=IfCondition(use_ekf),
        output='screen',
    )

    ekf = Node(
        package='lhr_state_estimation',
        executable='ekf_node',
        name='ekf_node',
        parameters=[{
            'init_x': LaunchConfiguration('init_x'),
            'init_y': LaunchConfiguration('init_y'),
            'init_yaw': LaunchConfiguration('init_yaw'),
        }],
        condition=IfCondition(use_ekf),
        output='screen',
    )

    control = Node(
        package='lhr_control',
        executable='pursuit_node',
        name='pure_pursuit',
        parameters=[{
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
            'truth_odom_topic': '/lhr/vehicle/odom_truth',
            'run_id': ParameterValue(LaunchConfiguration('run_id'), value_type=str),
            'output_csv': ParameterValue(
                LaunchConfiguration('output_csv'), value_type=str),
        }],
        output='screen',
        condition=IfCondition(LaunchConfiguration('enable_metrics')),
    )

    mission_mgr = Node(
        package='lhr_mission_manager',
        executable='mission_manager',
        name='mission_manager',
        parameters=[{
            'mission': LaunchConfiguration('mission'),
            'auto_go': LaunchConfiguration('auto_go'),
            'ready_hold_sec': LaunchConfiguration('ready_hold_sec'),
        }],
        output='screen',
    )

    recorder = ExecuteProcess(
        cmd=[
            'ros2', 'bag', 'record',
            '--output', [LaunchConfiguration('bag_dir'), '/',
                         LaunchConfiguration('run_id')],
            '--storage', 'mcap',
            '--custom-data', ['run_id=', LaunchConfiguration('run_id')],
            f'git_sha={_git_sha()}',
            '--topics', *RECORDED_TOPICS,
        ],
        output='screen',
        condition=IfCondition(LaunchConfiguration('record')),
    )

    foxglove_bridge = Node(
        package='foxglove_bridge',
        executable='foxglove_bridge',
        name='foxglove_bridge',
        parameters=[{'port': ParameterValue(
            LaunchConfiguration('foxglove_port'), value_type=int)}],
        output='screen',
        condition=IfCondition(LaunchConfiguration('foxglove')),
    )

    return LaunchDescription([
        seed_arg,
        lookahead_arg,
        metrics_arg,
        track_style_arg,
        num_wp_arg,
        a_lat_arg,
        v_min_arg,
        v_max_arg,
        max_accel_arg,
        max_decel_arg,
        fov_arg,
        range_arg,
        init_x_arg,
        init_y_arg,
        init_yaw_arg,
        mission_arg,
        auto_go_arg,
        ready_hold_arg,
        estimator_arg,
        gyro_bias_arg,
        wheel_scale_arg,
        run_id_arg,
        output_csv_arg,
        record_arg,
        bag_dir_arg,
        foxglove_arg,
        foxglove_port_arg,
        lidar_arg,
        mount_pitch_arg,
        return_profile_arg,
        elevation_profile_arg,
        motion_distortion_arg,
        clutter_profile_arg,
        cones,
        sensor_sim,
        lidar_sim,
        centerline,
        sim_truth,
        sim_ekf,
        inertial_sim,
        ekf,
        mission_mgr,
        control,
        metrics,
        recorder,
        foxglove_bridge,
    ])
