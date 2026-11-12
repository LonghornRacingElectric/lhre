"""FMU plant and Gazebo pose service actions shared by both perception modes."""

import math
import xml.etree.ElementTree as ET

from launch.actions import EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def world_spawn(world_path):
    """Read the rendered model origin, rather than recomputing a track spawn."""
    world = ET.parse(world_path).getroot().find('world')
    if world is None:
        raise ValueError('FMU mode needs one world element')
    vehicle = next((model for model in world.findall('include')
                    if model.findtext('uri') == 'model://fsae_vehicle'), None)
    if vehicle is None or vehicle.findtext('name', 'fsae_vehicle') != 'fsae_vehicle':
        raise ValueError('FMU mode requires the fsae_vehicle model include')
    pose = vehicle.find('pose')
    if pose is not None and pose.get('relative_to', ''):
        raise ValueError('FMU mode requires a world-relative vehicle spawn')
    values = [float(value) for value in (pose.text if pose is not None else '0 '*6).split()]
    if len(values) != 6 or not all(math.isfinite(value) for value in values):
        raise ValueError('Invalid FMU vehicle spawn')
    if any(abs(value) > 1e-9 for value in values[2:5]):
        raise ValueError('The planar FMU bridge requires spawn z, roll and pitch zero')
    return world.attrib['name'], (values[0], values[1], values[5])


def fmu_actions(context, world_path, truth_topic, use_ekf, fault_remaps):
    """Use Gazebo time and FMU truth with coordinated or asynchronous pose updates."""
    world, (x, y, yaw) = world_spawn(world_path)
    step_sec = float(context.launch_configurations.get('fmu_step_sec', '.02'))
    physics_sec = float(ET.parse(world_path).findtext('world/physics/max_step_size', 'nan'))
    if not (math.isfinite(step_sec) and math.isfinite(physics_sec)
            and 0 < physics_sec <= step_sec < .1):
        raise ValueError('Require 0 < Gazebo physics step <= fmu_step_sec < 0.1')
    if round(physics_sec*1e9) <= 0 or round(step_sec*1e9) % round(physics_sec*1e9):
        raise ValueError('fmu_step_sec must be a multiple of the world physics step')
    lockstep = context.launch_configurations.get('fmu_lockstep', 'true').lower() == 'true'
    plant = Node(
        package='lhr_sim_fmu', executable='sim_node', name='sim_fmu',
        parameters=[{
            'use_sim_time': True, 'external_clock': True, 'publish_clock': False,
            'init_x': x, 'init_y': y, 'init_yaw': yaw, 'update_hz': 1./step_sec,
            'fmu_path': ParameterValue(LaunchConfiguration('fmu_path'), value_type=str),
            'pedal_gain': ParameterValue(LaunchConfiguration('fmu_pedal_gain'), value_type=float),
            'command_timeout_sec': ParameterValue(
                LaunchConfiguration('fmu_command_timeout_sec'), value_type=float),
        }], remappings=[('/lhr/vehicle/odom', truth_topic),
                        *([('/tf', '/tf_truth')] if use_ekf else []),
                        *[remap for remap in fault_remaps.get('sim_fmu', [])
                          if remap[0] != '/lhr/vehicle/odom']], output='screen')
    pose = Node(
        package='lhr_gazebo', executable='fmu_pose', name='fmu_pose',
        parameters=[{
            'world_name': world, 'truth_topic': truth_topic, 'use_sim_time': True,
            'lockstep': ParameterValue(LaunchConfiguration('fmu_lockstep'), value_type=bool),
            'step_sec': ParameterValue(LaunchConfiguration('fmu_step_sec'), value_type=float),
            'physics_step_sec': physics_sec,
            'request_timeout_sec': ParameterValue(
                LaunchConfiguration('fmu_pose_timeout_sec'), value_type=float),
        }], output='screen')
    service = Node(
        package='ros_gz_bridge', executable='parameter_bridge', name='fmu_pose_service',
        arguments=[f'/world/{world}/set_pose@ros_gz_interfaces/srv/SetEntityPose',
                   *([f'/world/{world}/control@ros_gz_interfaces/srv/ControlWorld']
                     if lockstep else [])],
        output='screen')
    # A dead plant or pose bridge must never leave sensors running as a valid study.
    guards = [RegisterEventHandler(OnProcessExit(
        target_action=node, on_exit=[EmitEvent(event=Shutdown(
            reason=f'FMU simulation component exited: {name}'))]))
        for name, node in (('plant', plant), ('pose follower', pose), ('pose service', service))]
    return [*guards, service, plant, pose]
