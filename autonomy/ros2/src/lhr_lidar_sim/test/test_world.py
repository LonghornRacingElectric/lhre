"""Read the loaded Gazebo scene, rather than regenerating a seeded track."""

from lhr_lidar_sim.world import load_world_cones
import numpy as np
import pytest


def write_world(tmp_path, includes='', extra=''):
    """Write a minimal supported world with the same origins as our SDFs."""
    path = tmp_path / 'custom.sdf'
    path.write_text(f"""<sdf version="1.9"><world name="test">
      <model name="ground_plane"><static>true</static><link name="link">
        <collision name="ground"><geometry><plane>
          <normal>0 0 1</normal><size>200 200</size>
        </plane></geometry></collision></link></model>
      {includes}{extra}</world></sdf>""")
    return path


def cone(uri='cone_blue', pose='8 2 .1625 0 0 0'):
    """Describe a cone include in the generated-world convention."""
    return f'<include><uri>model://{uri}</uri><pose>{pose}</pose></include>'


def test_reads_exact_world_coordinates_and_cone_sizes(tmp_path):
    path = write_world(tmp_path, cone() + cone('cone_orange_large', '-3 4 .2525 0 0 0'))
    assert load_world_cones(path) == pytest.approx(np.array([
        [8., 2., .325, .228], [-3., 4., .505, .285]]))


@pytest.mark.parametrize('include, extra', [
    (cone('unmodelled_obstacle'), ''),
    (cone(pose='8 2 1 0 0 0'), ''),
    (cone(pose='8 2 .1625 0 .1 0'), ''),
    (cone(pose='8 2 .1625 0 0 .2'), ''),
    (cone(pose='nan 2 .1625 0 0 0'), ''),
    (cone(), '<model name="wall"/>'),
])
def test_rejects_geometry_the_caster_cannot_represent(tmp_path, include, extra):
    with pytest.raises(ValueError):
        load_world_cones(write_world(tmp_path, include, extra))


def test_requires_flat_ground_and_a_nonempty_track(tmp_path):
    path = write_world(tmp_path, cone())
    path.write_text(path.read_text().replace('0 0 1', '0 .1 1'))
    with pytest.raises(ValueError):
        load_world_cones(path)
    with pytest.raises(ValueError):
        load_world_cones(write_world(tmp_path))


@pytest.mark.parametrize('change', [
    lambda s: s.replace('<pose>8', '<pose relative_to="other">8'),
    lambda s: s.replace('</include>', '<scale>2 2 2</scale></include>'),
    lambda s: s.replace('<static>true</static>', '<pose>0 0 1 0 0 0</pose>'),
    lambda s: s.replace('</world>', '<frame name="other"/></world>'),
    lambda s: s.replace('</world>', '<actor name="pedestrian"/></world>'),
    lambda s: s.replace('</link>', '<visual name="wall"><geometry><box>'
                        '<size>1 1 1</size></box></geometry></visual></link>'),
])
def test_rejects_unrepresented_scene_transforms_and_surfaces(tmp_path, change):
    path = write_world(tmp_path, cone())
    path.write_text(change(path.read_text()))
    with pytest.raises(ValueError):
        load_world_cones(path)


def test_accepts_equivalent_numeric_ground_normal(tmp_path):
    path = write_world(tmp_path, cone())
    path.write_text(path.read_text().replace('0 0 1', '0.0 0.0 1.0'))
    assert len(load_world_cones(path)) == 1
