"""Check that Ogre-based windows use XWayland when Qt prefers Wayland."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.utilities import perform_substitutions
from launch_ros.actions import Node
import pytest

PKG_DIR = Path(__file__).resolve().parents[1]


def test_packaged_fontconfig_excludes_user_fonts(tmp_path):
    """System fonts.conf would silently reintroduce the crashing user font path."""
    if shutil.which('fc-list') is None:
        pytest.skip('Fontconfig tools are not installed')
    config = PKG_DIR / 'config' / 'gazebo-fontconfig.conf'
    env = {**os.environ, 'FONTCONFIG_FILE': str(config),
           'XDG_CACHE_HOME': str(tmp_path / 'cache')}
    fonts = subprocess.check_output(
        ['fc-list', '--format', '%{file}\n'], env=env, text=True).splitlines()
    if not fonts:
        pytest.skip('No system fonts are installed')
    user_font = tmp_path / 'user-data' / 'fonts' / Path(fonts[0]).name
    user_font.parent.mkdir(parents=True)
    shutil.copyfile(fonts[0], user_font)
    env['XDG_DATA_HOME'] = str(tmp_path / 'user-data')
    isolated = subprocess.check_output(
        ['fc-list', '--format', '%{file}\n'], env=env, text=True).splitlines()
    assert isolated
    assert all(Path(path).is_relative_to('/usr/share/fonts') for path in isolated)


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
