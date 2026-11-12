"""The viewer must show the rendered world, independent of track seed defaults."""

from pathlib import Path
import xml.etree.ElementTree as ET

from lhr_gazebo.world_viz import reference_path, world_markers
from lhr_metrics.reference import paired_centerline, world_geometry
from lhr_trackgen.cone_geometry import CONE_SPECS
import pytest


@pytest.mark.parametrize('filename', ['autocross_seed2.sdf', 'acceleration_seed1.sdf'])
def test_markers_match_actual_world_including_custom_positions(tmp_path, filename):
    source = Path(__file__).parents[1] / 'worlds' / filename
    world = tmp_path / 'custom.sdf'
    world.write_text(source.read_text())
    acceleration = filename.startswith('acceleration')
    _, _, cones = world_geometry(world, acceleration=acceleration)
    # Change an actual SDF coordinate; regenerating from the seed would miss it.
    y = cones[0][2]
    tree = ET.parse(world)
    pose = tree.find(f"./world/include[name='{cones[0][0]}']/pose")
    values = pose.text.split()
    values[1] = str(y + .123)
    pose.text = ' '.join(values)
    tree.write(world)
    left, right, cones = world_geometry(world, acceleration=acceleration)
    markers = world_markers(world, acceleration).markers
    assert len(markers) == len(cones)
    for marker, (name, x, y, _, kind) in zip(markers, cones):
        spec = CONE_SPECS[kind]
        assert marker.header.frame_id == 'map'
        assert marker.text == name
        assert (marker.pose.position.x, marker.pose.position.y) == (x, y)
        assert marker.scale.z == spec.height_m
        assert marker.color.a < 1.0
    path = reference_path(world, acceleration)
    expected = paired_centerline(left, right)
    if not acceleration:
        expected.append(expected[0])
    assert path.header.frame_id == 'map'
    assert [(p.pose.position.x, p.pose.position.y) for p in path.poses] == expected
