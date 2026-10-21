"""Check that Ogre-based windows use XWayland when Qt prefers Wayland."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.utilities import perform_substitutions
from launch_ros.actions import Node
import pytest

PKG_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('platform, wayland, display, expected', [
    ('wayland;xcb', 'wayland-1', ':1', 'xcb'),
    ('wayland', 'wayland-1', ':1', 'xcb'),
    ('', 'wayland-1', ':1', 'xcb'),
    ('xcb', 'wayland-1', ':1', None),
    ('offscreen', 'wayland-1', ':1', None),
    ('wayland;xcb', '', ':1', None),
])
@pytest.mark.parametrize('fontconfig', [None, '/tmp/custom-fontconfig.conf'])
def test_graphical_nodes_use_xwayland_when_needed(
    platform, wayland, display, expected, fontconfig, monkeypatch,
):
    """Gazebo and RViz must choose the same working Qt backend."""
    for key, value in [
        ('QT_QPA_PLATFORM', platform),
        ('WAYLAND_DISPLAY', wayland),
        ('DISPLAY', display),
    ]:
        if value:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)

    if fontconfig:
        monkeypatch.setenv('FONTCONFIG_FILE', fontconfig)
    else:
        monkeypatch.delenv('FONTCONFIG_FILE', raising=False)

    launch_path = PKG_DIR / 'launch' / 'gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_demo', launch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: '/tmp/lhr_gazebo')

    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    actions = module._launch_setup(context)
    gazebo = next(action for action in actions
                  if type(action) is ExecuteProcess)
    rviz = next(action for action in actions
                if isinstance(action, Node) and action.node_package == 'rviz2')

    for action in (gazebo, rviz):
        added_env = {
            perform_substitutions(context, key):
                perform_substitutions(context, value)
            for key, value in action.additional_env
        }
        assert added_env.get('QT_QPA_PLATFORM') == expected
        if action is gazebo:
            assert added_env['FONTCONFIG_FILE'] == (
                fontconfig or '/tmp/lhr_gazebo/config/gazebo-fontconfig.conf')
