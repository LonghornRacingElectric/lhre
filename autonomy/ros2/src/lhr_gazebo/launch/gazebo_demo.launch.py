"""
Launch the FSAE autonomy stack with Gazebo Harmonic physics simulation.

Replaces lhr_sim_kinematic with Gazebo for physics and odometry.
Perception is selectable: 'sim' (trackgen + sensor_sim) or 'lidar' (LiDAR).
The upper stack (track_builder, control, mission_manager, metrics) is unchanged.

Usage:
    ros2 launch lhr_gazebo gazebo_demo.launch.py
    ros2 launch lhr_gazebo gazebo_demo.launch.py gui:=false
    ros2 launch lhr_gazebo gazebo_demo.launch.py perception:=lidar
    ros2 launch lhr_gazebo gazebo_demo.launch.py track_style:=oval
    ros2 launch lhr_gazebo gazebo_demo.launch.py track_style:=oval perception:=lidar
    ros2 launch lhr_gazebo gazebo_demo.launch.py foxglove:=true rviz:=false
    ros2 launch lhr_gazebo gazebo_demo.launch.py gui:=false record:=true
"""

import os
from pathlib import Path
import subprocess
import time

from ament_index_python.packages import get_package_share_directory

from launch import LaunchContext, LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from lhr_vehicle import load_vehicle


# What record:=true keeps: enough to replay a run in the Foxglove layout
# (foxglove/lhr_gazebo.json) and to rescore it. /tf_truth only exists under
# estimator:=ekf; topics a run never publishes are simply absent.
RECORDED_TOPICS = (
    '/clock', '/tf', '/tf_static', '/tf_truth',
    '/lhr/lidar/points', '/lhr/sensor/cones_detected', '/lhr/track/cones',
    '/lhr/track/centerline', '/lhr/vehicle/odom',
    '/lhr/vehicle/odom_truth', '/lhr/vehicle/cmd', '/lhr/vehicle/body',
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


def _find_world(context: LaunchContext):
    """
    Resolve the world SDF path from track_style and seed.

    Search order:
      1. Explicit ``world`` argument (if the user overrode it)
      2. ``<worlds_dir>/<track_style>_seed<seed>.sdf`` in the source tree
      3. Same pattern in the installed share directory
      4. Fall back to any SDF in the source worlds directory
    """
    world_arg = context.launch_configurations.get('world', '')
    track_style = context.launch_configurations.get('track_style', 'autocross')
    seed = context.launch_configurations.get('seed', '1')

    # If the user explicitly passed a valid world path, use it
    if world_arg and os.path.isfile(world_arg):
        return world_arg

    expected = f'{track_style}_seed{seed}.sdf'

    # Source tree (launch/ is one level below the package root)
    launch_dir = os.path.dirname(os.path.abspath(__file__))
    src_worlds = os.path.join(launch_dir, os.pardir, 'worlds')
    src_path = os.path.join(src_worlds, expected)
    if os.path.isfile(src_path):
        return os.path.abspath(src_path)

    # Installed share directory
    try:
        pkg_share = get_package_share_directory('lhr_gazebo')
        share_path = os.path.join(pkg_share, 'worlds', expected)
        if os.path.isfile(share_path):
            return share_path
    except Exception:
        pass

    # Fallback: first SDF found in source worlds directory
    if os.path.isdir(src_worlds):
        for f in sorted(os.listdir(src_worlds)):
            if f.endswith('.sdf'):
                return os.path.abspath(os.path.join(src_worlds, f))

    return ''


def _launch_setup(context: LaunchContext):
    """
    Build all launch actions.

    Called via OpaqueFunction so we can resolve the world path from
    track_style + seed at launch time.
    """
    world_path = _find_world(context)
    if not world_path:
        raise RuntimeError(
            'No world SDF found. Generate one with: '
            'python3 scripts/generate_world.py --style <style> --seed <seed>')

    pkg_share = get_package_share_directory('lhr_gazebo')
    models_dir = os.path.join(pkg_share, 'models')
    config_dir = os.path.join(pkg_share, 'config')

    perception = context.launch_configurations.get('perception', 'sim')
    classify_sides = context.launch_configurations.get(
        'lidar_classify_sides', 'false').lower() == 'true'
    use_ekf = context.launch_configurations.get(
        'estimator', 'truth').lower() == 'ekf'

    _gz_env = {
        'GZ_SIM_RESOURCE_PATH': models_dir,
    }

    # ----- Gazebo server -----
    gui = context.launch_configurations.get('gui', 'true').lower()
    if gui == 'true':
        gz_cmd = ['gz', 'sim', '-r', world_path]
    else:
        gz_cmd = ['gz', 'sim', '-r', '-s', world_path]

    gz_sim = ExecuteProcess(
        cmd=gz_cmd,
        output='screen',
        additional_env=_gz_env,
    )

    # ----- ros_gz_bridge -----
    # Under 'ekf' the ground truth Gazebo publishes steps aside so the
    # estimator owns /lhr/vehicle/odom and the map -> base_link transform.
    bridge_remaps = []
    if use_ekf:
        bridge_remaps = [
            ('/lhr/vehicle/odom', '/lhr/vehicle/odom_truth'),
            ('/tf', '/tf_truth'),
        ]

    bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        name='ros_gz_bridge',
        parameters=[{
            'config_file': os.path.join(config_dir, 'ros_gz_bridge.yaml'),
        }],
        remappings=bridge_remaps,
        output='screen',
    )

    # ----- State estimation -----
    estimation_nodes = []
    if use_ekf:
        # Gazebo already bridges a physics IMU, so only the wheel speeds
        # are synthesized here.
        estimation_nodes.append(Node(
            package='lhr_sensor_sim',
            executable='inertial_sim',
            name='inertial_sim',
            parameters=[{
                'publish_imu': False,
                'gyro_bias': LaunchConfiguration('gyro_bias'),
                'wheel_scale_error': LaunchConfiguration(
                    'wheel_scale_error'),
                'use_sim_time': True,
            }],
            output='screen',
        ))
        estimation_nodes.append(Node(
            package='lhr_state_estimation',
            executable='ekf_node',
            name='ekf_node',
            parameters=[{
                # The world file picks the spawn pose, so seed from the
                # first truth sample rather than from a launch argument.
                'init_pose_topic': '/lhr/vehicle/odom_truth',
                'use_sim_time': True,
            }],
            output='screen',
        ))

    # ----- Joint command adapter -----
    cmd_adapter = Node(
        package='lhr_gazebo',
        executable='joint_cmd_adapter',
        name='joint_cmd_adapter',
        parameters=[{'use_sim_time': True}],
        output='screen',
    )

    # ----- Ground-truth cones -----
    # The same generator that built the world, so these match it cone for
    # cone. Under perception:=sim they feed sensor_sim. Under lidar nothing
    # in the stack reads them; they are there for viewers and scoring.
    truth_cones = Node(
        package='lhr_trackgen',
        executable='publish_cones',
        name='publish_cones',
        parameters=[{
            'seed': LaunchConfiguration('seed'),
            'track_style': LaunchConfiguration('track_style'),
            'num_waypoints': LaunchConfiguration('num_waypoints'),
            'use_sim_time': True,
        }],
        output='screen',
    )

    # ----- Perception nodes -----
    perception_nodes = []

    if perception == 'sim':
        perception_nodes.append(Node(
            package='lhr_sensor_sim',
            executable='sensor_sim',
            name='sensor_sim',
            parameters=[{
                'fov_deg': LaunchConfiguration('fov_deg'),
                'max_range_m': LaunchConfiguration('max_range_m'),
                'use_sim_time': True,
            }],
            output='screen',
        ))
    elif perception == 'lidar':
        perception_nodes.append(Node(
            package='lhr_perception',
            executable='lidar_cone_detector',
            name='lidar_cone_detector',
            parameters=[{
                'use_sim_time': True,
                'max_range': 20.0,
                'min_range': 0.8,
                'ground_z_min': -0.40,
                'ground_z_max': 0.5,
                'cluster_radius': 0.5,
                'min_cluster_points': 1,
                'dedup_radius': 1.5,
                'classify_sides': classify_sides,
            }],
            output='screen',
        ))

    # ----- Track builder -----
    if perception == 'lidar':
        centerline = Node(
            package='lhr_track_builder',
            executable='track_builder',
            name='track_builder',
            parameters=[{
                'use_sim_time': True,
                'pairing_strategy': (
                    'classified' if classify_sides else 'boundary'),
                'chaining_alg': ParameterValue(
                    LaunchConfiguration('chaining_alg'), value_type=str),
                'min_step_m': ParameterValue(
                    LaunchConfiguration('min_step_m'), value_type=float),
                'max_step_m': ParameterValue(
                    LaunchConfiguration('max_step_m'), value_type=float),
                'max_turn_deg': ParameterValue(
                    LaunchConfiguration('max_turn_deg'), value_type=float),
                'distance_weight': ParameterValue(
                    LaunchConfiguration('distance_weight'), value_type=float),
                'heading_weight': ParameterValue(
                    LaunchConfiguration('heading_weight'), value_type=float),
                'beam_width': ParameterValue(
                    LaunchConfiguration('beam_width'), value_type=int),
                'beam_depth': ParameterValue(
                    LaunchConfiguration('beam_depth'), value_type=int),
                'track_width': 3.5,
                'track_width_tolerance': 1.0,
            }],
            output='screen',
        )
    else:
        centerline = Node(
            package='lhr_track_builder',
            executable='track_builder',
            name='track_builder',
            parameters=[{
                'use_sim_time': True,
                'pairing_strategy': 'boundary',
                'chaining_alg': ParameterValue(
                    LaunchConfiguration('chaining_alg'), value_type=str),
                'min_step_m': ParameterValue(
                    LaunchConfiguration('min_step_m'), value_type=float),
                'max_step_m': ParameterValue(
                    LaunchConfiguration('max_step_m'), value_type=float),
                'max_turn_deg': ParameterValue(
                    LaunchConfiguration('max_turn_deg'), value_type=float),
                'distance_weight': ParameterValue(
                    LaunchConfiguration('distance_weight'), value_type=float),
                'heading_weight': ParameterValue(
                    LaunchConfiguration('heading_weight'), value_type=float),
                'beam_width': ParameterValue(
                    LaunchConfiguration('beam_width'), value_type=int),
                'beam_depth': ParameterValue(
                    LaunchConfiguration('beam_depth'), value_type=int),
            }],
            output='screen',
        )

    # ----- Control -----
    control = Node(
        package='lhr_control',
        executable='pursuit_node',
        name='pure_pursuit',
        parameters=[{
            'lookahead_dist': LaunchConfiguration('lookahead_dist'),
            'lookahead_min': LaunchConfiguration('lookahead_min'),
            'lookahead_curvature_gain': LaunchConfiguration(
                'lookahead_curvature_gain'),
            'a_lat_max': LaunchConfiguration('a_lat_max'),
            'v_min': LaunchConfiguration('v_min'),
            'v_max': LaunchConfiguration('v_max'),
            'max_accel': LaunchConfiguration('max_accel'),
            'max_decel': LaunchConfiguration('max_decel'),
            'use_sim_time': True,
        }],
        output='screen',
    )

    # ----- Metrics -----
    metrics_nodes = []
    if context.launch_configurations.get(
            'enable_metrics', 'true').lower() == 'true':
        metrics_nodes.append(Node(
            package='lhr_metrics',
            executable='metrics_node',
            name='metrics_node',
            parameters=[{
                'truth_odom_topic': '/lhr/vehicle/odom_truth',
                # Shared with the bag under record:=true, so a row and its
                # recording can be matched.
                'run_id': ParameterValue(
                    LaunchConfiguration('run_id'), value_type=str),
                'output_csv': ParameterValue(
                    LaunchConfiguration('output_csv'), value_type=str),
                'use_sim_time': True,
            }],
            output='screen',
        ))

    # ----- Viewer support -----
    # The cloud is stamped in 'lidar' (gz_frame_id in model.sdf.in), so a
    # viewer needs that frame on the tree. The mount comes from the same
    # vehicle YAML that places the sensor in the SDF.
    lidar_x, lidar_y, lidar_z = load_vehicle().lidar_position_m
    lidar_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='lidar_mount_tf',
        arguments=[
            '--x', str(lidar_x), '--y', str(lidar_y), '--z', str(lidar_z),
            '--frame-id', 'base_link', '--child-frame-id', 'lidar',
        ],
        parameters=[{'use_sim_time': True}],
        output='screen',
    )

    vehicle_viz = Node(
        package='lhr_vehicle',
        executable='vehicle_viz',
        name='vehicle_viz',
        parameters=[{
            # The node defaults to the CAD mesh, which is not on main yet.
            'use_mesh': ParameterValue(
                LaunchConfiguration('vehicle_mesh'), value_type=bool),
            'use_sim_time': True,
        }],
        output='screen',
    )

    viewer_nodes = []
    if context.launch_configurations.get('foxglove', 'false').lower() == 'true':
        viewer_nodes.append(Node(
            package='foxglove_bridge',
            executable='foxglove_bridge',
            name='foxglove_bridge',
            parameters=[{
                'port': ParameterValue(
                    LaunchConfiguration('foxglove_port'), value_type=int),
                'use_sim_time': True,
            }],
            output='screen',
        ))
    if context.launch_configurations.get('record', 'false').lower() == 'true':
        run_id = context.launch_configurations['run_id']
        bag_dir = context.launch_configurations['bag_dir']
        viewer_nodes.append(ExecuteProcess(
            cmd=[
                'ros2', 'bag', 'record',
                '--output', f'{bag_dir}/{run_id}',
                '--storage', 'mcap',
                '--custom-data', f'run_id={run_id}', f'git_sha={_git_sha()}',
                '--topics', *RECORDED_TOPICS,
            ],
            output='screen',
        ))

    # ----- RViz -----
    rviz_nodes = []
    if context.launch_configurations.get('rviz', 'true').lower() == 'true':
        rviz_nodes.append(Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            arguments=['-d', os.path.join(config_dir, 'default.rviz')],
            parameters=[{'use_sim_time': True}],
            output='screen',
        ))

    # ----- Mission manager -----
    mission_mgr = Node(
        package='lhr_mission_manager',
        executable='mission_manager',
        name='mission_manager',
        parameters=[{
            'mission': LaunchConfiguration('mission'),
            'auto_go': LaunchConfiguration('auto_go'),
            'ready_hold_sec': LaunchConfiguration('ready_hold_sec'),
            'use_sim_time': True,
        }],
        output='screen',
    )

    return [
        SetEnvironmentVariable('GZ_SIM_RESOURCE_PATH', models_dir),
        gz_sim,
        bridge,
        cmd_adapter,
        *estimation_nodes,
        truth_cones,
        *perception_nodes,
        centerline,
        mission_mgr,
        control,
        *metrics_nodes,
        lidar_tf,
        vehicle_viz,
        *viewer_nodes,
        *rviz_nodes,
    ]


def generate_launch_description():
    return LaunchDescription([
        # ----- Launch arguments -----
        DeclareLaunchArgument(
            'world', default_value='',
            description='Override: absolute path to world SDF file'),
        DeclareLaunchArgument(
            'gui', default_value='true',
            description='Launch Gazebo GUI (set false for headless)'),
        DeclareLaunchArgument(
            'perception', default_value='sim',
            description='Perception mode: "sim" or "lidar"'),
        DeclareLaunchArgument(
            'lidar_classify_sides', default_value='false',
            description='Infer LiDAR cone sides (opt in; false publishes unlabeled cones)'),
        DeclareLaunchArgument(
            'track_style', default_value='autocross',
            description='Track style: "autocross", "oval", or "simple"'),
        DeclareLaunchArgument(
            'estimator', default_value='truth',
            description='Odometry source: "truth" (Gazebo) or "ekf"'),
        DeclareLaunchArgument(
            'gyro_bias', default_value='0.0',
            description='Simulated gyro bias (rad/s); drives heading drift'),
        DeclareLaunchArgument(
            'wheel_scale_error', default_value='1.0',
            description='Simulated rolling-radius error (1.0 = exact)'),
        DeclareLaunchArgument('seed', default_value='1'),
        DeclareLaunchArgument('num_waypoints', default_value='10'),
        # Sensor sim
        DeclareLaunchArgument('fov_deg', default_value='200.0'),
        DeclareLaunchArgument('max_range_m', default_value='20.0'),
        # Track builder chaining (both perception modes)
        DeclareLaunchArgument('chaining_alg', default_value='constrained_greedy'),
        DeclareLaunchArgument('min_step_m', default_value='0.3'),
        DeclareLaunchArgument('max_step_m', default_value='4.0'),
        DeclareLaunchArgument('max_turn_deg', default_value='70.0'),
        DeclareLaunchArgument('distance_weight', default_value='1.0'),
        DeclareLaunchArgument('heading_weight', default_value='1.0'),
        DeclareLaunchArgument('beam_width', default_value='3'),
        DeclareLaunchArgument('beam_depth', default_value='10'),
        # Control
        DeclareLaunchArgument('lookahead_dist', default_value='5.0'),
        DeclareLaunchArgument('lookahead_min', default_value='2.0'),
        DeclareLaunchArgument('lookahead_curvature_gain', default_value='3.0'),
        DeclareLaunchArgument('a_lat_max', default_value='3.0'),
        DeclareLaunchArgument('v_min', default_value='1.0'),
        DeclareLaunchArgument('v_max', default_value='5.0'),
        DeclareLaunchArgument('max_accel', default_value='1.0'),
        DeclareLaunchArgument('max_decel', default_value='2.0'),
        # RViz / metrics
        DeclareLaunchArgument('rviz', default_value='true'),
        DeclareLaunchArgument('enable_metrics', default_value='true'),
        # Metrics row and bag share run_id. The T keeps YAML from reading
        # an all-digit ID as a number.
        DeclareLaunchArgument(
            'run_id', default_value=time.strftime('%Y%m%dT%H%M%S')),
        DeclareLaunchArgument('output_csv', default_value='data/metrics.csv'),
        # Foxglove and recording (same names as lhr_demo's mvs_demo)
        DeclareLaunchArgument(
            'foxglove', default_value='false',
            description='Serve the live graph to Foxglove on foxglove_port'),
        DeclareLaunchArgument('foxglove_port', default_value='8765'),
        DeclareLaunchArgument(
            'record', default_value='false',
            description='Record the run to MCAP under bag_dir/run_id'),
        DeclareLaunchArgument('bag_dir', default_value='data/bags'),
        DeclareLaunchArgument(
            'vehicle_mesh', default_value='false',
            description='Draw the CAD mesh instead of box + wheels'),
        # Mission
        DeclareLaunchArgument('mission', default_value='autocross'),
        DeclareLaunchArgument('auto_go', default_value='true'),
        DeclareLaunchArgument('ready_hold_sec', default_value='5.0'),
        # ----- Build all nodes via OpaqueFunction -----
        OpaqueFunction(function=_launch_setup),
    ])
