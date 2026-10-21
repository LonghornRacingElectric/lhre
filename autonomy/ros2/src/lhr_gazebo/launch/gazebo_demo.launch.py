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
"""

import os

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
import yaml


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


def _fault_remaps(fault: str, use_ekf: bool, configs: dict) -> dict:
    """
    Where fault:=<kind> splices fault_injector in: node name -> remappings.

    A topic fault moves the producer (or, for steer_freeze, the consumer) onto
    a side topic so the injector sits in the middle. Process faults need no
    rewiring. No fault means no change to any node. A fault that cannot trip
    under the other launch arguments fails the launch.
    """
    if not fault:
        return {}
    from lhr_gazebo.fault_injector import FAULTS
    if fault not in FAULTS:
        raise RuntimeError(
            f'Unknown fault {fault!r}, expected one of {sorted(FAULTS)}')
    for arg, value in FAULTS[fault].needs:
        if configs.get(arg, '').lower() != value:
            raise RuntimeError(f'fault:={fault} can only trip with {arg}:={value}')
    odom_raw = [('/lhr/vehicle/odom', '/lhr/vehicle/odom_raw')]
    # Under ekf the bridge's odom is already truth; corrupt the estimate.
    odom_owner = 'ekf_node' if use_ekf else 'ros_gz_bridge'
    return {
        'odom_nan': {odom_owner: odom_raw},
        'odom_spike': {odom_owner: odom_raw},
        'lidar_drop': {'ros_gz_bridge': [
            ('/lhr/lidar/points', '/lhr/lidar/points_raw')]},
        'cmd_nan': {'pure_pursuit': [
            ('/lhr/vehicle/cmd', '/lhr/vehicle/cmd_raw')]},
        'steer_freeze': {'joint_cmd_adapter': [
            ('/lhr/vehicle/cmd', '/lhr/vehicle/cmd_actuated')]},
    }.get(fault, {})


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
    fault = context.launch_configurations.get('fault', '')
    fault_remaps = _fault_remaps(fault, use_ekf, context.launch_configurations)

    enable_stereo = context.launch_configurations.get(
        'enable_stereo_camera', 'false').lower() == 'true'
    camera_nodes = []
    enable_color = context.launch_configurations.get(
        'enable_camera_color_classifier', 'false').lower() == 'true'
    if enable_color and not enable_stereo:
        raise RuntimeError('Camera classification requires enable_stereo_camera:=true')
    if enable_stereo:
        from lhr_gazebo.stereo_camera import stereo_actions
        from lhr_vehicle import load_vehicle
        camera_nodes = stereo_actions(load_vehicle().stereo_camera, config_dir)
    if enable_color:
        from lhr_perception.cone_color import DEFAULT_THRESHOLDS, validate_config
        thresholds = {
            color: yaml.safe_load(context.launch_configurations[f'cone_color_{color}_hsv'])
            for color in DEFAULT_THRESHOLDS
        }
        min_area = int(context.launch_configurations['cone_color_min_region_area'])
        validate_config(thresholds, min_area)
        camera_nodes.append(Node(
            package='lhr_perception',
            executable='camera_color_classifier',
            name='camera_color_classifier',
            parameters=[{
                'use_sim_time': True,
                'min_region_area': ParameterValue(
                    LaunchConfiguration('cone_color_min_region_area'), value_type=int),
                **{f'{color}_hsv': bounds for color, bounds in thresholds.items()},
            }],
            output='screen',
        ))

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
        remappings=bridge_remaps + fault_remaps.get('ros_gz_bridge', []),
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
            remappings=fault_remaps.get('ekf_node', []),
            output='screen',
        ))

    # ----- Joint command adapter -----
    cmd_adapter = Node(
        package='lhr_gazebo',
        executable='joint_cmd_adapter',
        name='joint_cmd_adapter',
        parameters=[{
            'cmd_timeout_s': ParameterValue(
                LaunchConfiguration('adapter_cmd_timeout_s'), value_type=float),
            'use_sim_time': True,
        }],
        remappings=fault_remaps.get('joint_cmd_adapter', []),
        output='screen',
    )

    # ----- Perception nodes -----
    perception_nodes = []

    if perception == 'sim':
        perception_nodes.append(Node(
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
        ))
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
                'cluster_radius': 0.5,
                'ground_z_min': ParameterValue(
                    LaunchConfiguration('lidar_ground_z_min'), value_type=float),
                'ground_z_max': ParameterValue(
                    LaunchConfiguration('lidar_ground_z_max'), value_type=float),
                'min_cluster_points': ParameterValue(
                    LaunchConfiguration('lidar_min_cluster_points'), value_type=int),
                'dedup_radius': ParameterValue(
                    LaunchConfiguration('lidar_dedup_radius'), value_type=float),
                'max_cluster_points': ParameterValue(
                    LaunchConfiguration('lidar_max_cluster_points'), value_type=int),
                'stack_window_sec': ParameterValue(
                    LaunchConfiguration('lidar_stack_window_sec'), value_type=float),
                'stack_max_frames': ParameterValue(
                    LaunchConfiguration('lidar_stack_max_frames'), value_type=int),
                'transform_wait_sec': ParameterValue(
                    LaunchConfiguration('lidar_transform_wait_sec'), value_type=float),
                'sensor_transform_source': ParameterValue(
                    LaunchConfiguration('lidar_sensor_transform_source'), value_type=str),
                'height_filter_frame': ParameterValue(
                    LaunchConfiguration('lidar_height_filter_frame'), value_type=str),
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
            'closed_path': perception != 'lidar',
        }],
        remappings=fault_remaps.get('pure_pursuit', []),
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
                'use_sim_time': True,
            }],
            output='screen',
        ))

    # ----- RViz -----
    rviz_nodes = []
    if context.launch_configurations.get('rviz', 'true').lower() == 'true':
        rviz_nodes.append(Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            arguments=['-d', os.path.join(
                config_dir, 'stereo.rviz' if enable_stereo else 'default.rviz')],
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

    # ----- Safety monitor -----
    safety_nodes = []
    if context.launch_configurations.get('safety', 'false').lower() == 'true':
        def _float(name):
            return ParameterValue(LaunchConfiguration(name), value_type=float)

        safety_nodes.append(Node(
            package='lhr_safety',
            executable='safety_node',
            name='safety_node',
            parameters=[{
                # Follows the perception mode rather than being an argument:
                # the Gazebo LiDAR always publishes, but under
                # perception:=sim nothing reads the cloud.
                'monitor_lidar': perception == 'lidar',
                'lidar_timeout_s': _float('safety_lidar_timeout_s'),
                'odom_timeout_s': _float('safety_odom_timeout_s'),
                'cmd_timeout_s': _float('safety_cmd_timeout_s'),
                'max_position_std_m': _float('safety_max_position_std_m'),
                'max_speed_factor': _float('safety_max_speed_factor'),
                'geofence_radius_m': _float('safety_geofence_radius_m'),
                'steer_min_speed_mps': _float('safety_steer_min_speed_mps'),
                'steer_yaw_rate_tol': _float('safety_steer_yaw_rate_tol'),
                'steer_rel_tol': _float('safety_steer_rel_tol'),
                'steer_window_s': _float('safety_steer_window_s'),
                'use_sim_time': True,
            }],
            output='screen',
        ))

    # ----- Fault injection (safety testing, #117; sim only) -----
    fault_nodes = []
    if fault:
        fault_nodes.append(Node(
            package='lhr_gazebo',
            executable='fault_injector',
            name='fault_injector',
            parameters=[{
                'fault': ParameterValue(LaunchConfiguration('fault'),
                                        value_type=str),
                'fault_after_s': ParameterValue(
                    LaunchConfiguration('fault_after_s'), value_type=float),
                'fault_duration_s': ParameterValue(
                    LaunchConfiguration('fault_duration_s'), value_type=float),
                'use_sim_time': True,
            }],
            output='screen',
        ))

    return [
        SetEnvironmentVariable('GZ_SIM_RESOURCE_PATH', models_dir),
        gz_sim,
        bridge,
        cmd_adapter,
        *camera_nodes,
        *estimation_nodes,
        *perception_nodes,
        centerline,
        mission_mgr,
        control,
        *metrics_nodes,
        *safety_nodes,
        *fault_nodes,
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
        DeclareLaunchArgument(
            'enable_stereo_camera', default_value='false',
            choices=['true', 'false'],
            description='Bridge ZED 2i stereo images/calibration and camera TF'),
        DeclareLaunchArgument(
            'enable_camera_color_classifier', default_value='false',
            choices=['true', 'false'],
            description='Label ZED color regions; requires enable_stereo_camera'),
        DeclareLaunchArgument(
            'cone_color_min_region_area', default_value='25',
            description='Minimum connected color region area in pixels'),
        DeclareLaunchArgument(
            'cone_color_blue_hsv', default_value='[100, 100, 50, 130, 255, 255]',
            description='Blue HSV bounds [h_min, s_min, v_min, h_max, s_max, v_max]'),
        DeclareLaunchArgument(
            'cone_color_yellow_hsv', default_value='[20, 100, 50, 35, 255, 255]',
            description='Yellow HSV bounds [h_min, s_min, v_min, h_max, s_max, v_max]'),
        DeclareLaunchArgument(
            'cone_color_orange_hsv', default_value='[5, 100, 50, 19, 255, 255]',
            description='Orange HSV bounds [h_min, s_min, v_min, h_max, s_max, v_max]'),
        # Sensor sim
        DeclareLaunchArgument('fov_deg', default_value='200.0'),
        DeclareLaunchArgument('max_range_m', default_value='20.0'),
        # Gazebo keeps its sensor-frame detector defaults.
        DeclareLaunchArgument('lidar_ground_z_min', default_value='-0.40'),
        DeclareLaunchArgument('lidar_ground_z_max', default_value='0.5'),
        DeclareLaunchArgument('lidar_min_cluster_points', default_value='1'),
        DeclareLaunchArgument('lidar_dedup_radius', default_value='1.5'),
        DeclareLaunchArgument('lidar_max_cluster_points', default_value='50'),
        DeclareLaunchArgument('lidar_stack_window_sec', default_value='0.0'),
        DeclareLaunchArgument('lidar_stack_max_frames', default_value='10'),
        DeclareLaunchArgument('lidar_transform_wait_sec', default_value='0.5'),
        DeclareLaunchArgument('lidar_sensor_transform_source', default_value='vehicle_config'),
        DeclareLaunchArgument('lidar_height_filter_frame', default_value='sensor'),
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
        # Mission
        DeclareLaunchArgument('mission', default_value='autocross'),
        DeclareLaunchArgument('auto_go', default_value='true'),
        DeclareLaunchArgument('ready_hold_sec', default_value='5.0'),
        # Joint adapter: stop the wheels when commands stop (0 disables)
        DeclareLaunchArgument('adapter_cmd_timeout_s', default_value='0.3'),
        # Fault injection for safety testing (#117). Empty = no injector.
        DeclareLaunchArgument(
            'fault', default_value='',
            description='One fault_injector kind, e.g. ekf_kill, lidar_drop'),
        DeclareLaunchArgument('fault_after_s', default_value='30.0'),
        DeclareLaunchArgument('fault_duration_s', default_value='0.0'),
        # Safety monitor (lhr_safety). Off until #117's fault injection
        # has tuned the thresholds; defaults match the node's.
        DeclareLaunchArgument(
            'safety', default_value='false',
            description='Run safety_node: trips the mission on stack faults'),
        DeclareLaunchArgument('safety_lidar_timeout_s', default_value='0.3'),
        DeclareLaunchArgument('safety_odom_timeout_s', default_value='0.25'),
        DeclareLaunchArgument('safety_cmd_timeout_s', default_value='0.25'),
        DeclareLaunchArgument('safety_max_position_std_m', default_value='0.0'),
        DeclareLaunchArgument('safety_max_speed_factor', default_value='1.5'),
        DeclareLaunchArgument('safety_geofence_radius_m', default_value='150.0'),
        DeclareLaunchArgument('safety_steer_min_speed_mps', default_value='2.0'),
        DeclareLaunchArgument('safety_steer_yaw_rate_tol', default_value='0.15'),
        DeclareLaunchArgument('safety_steer_rel_tol', default_value='0.5'),
        DeclareLaunchArgument('safety_steer_window_s', default_value='1.0'),
        # ----- Build all nodes via OpaqueFunction -----
        OpaqueFunction(function=_launch_setup),
    ])
