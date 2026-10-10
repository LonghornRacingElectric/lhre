"""Run the ROS Python docstring checker."""

from ament_pep257.main import main
import pytest


@pytest.mark.linter
@pytest.mark.pep257
def test_pep257():
    """Check package Python docstrings with pep257."""
    assert main(argv=['.']) == 0
