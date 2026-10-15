import importlib.util
import types
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]

# Made-up coefficients for tests only. They are not a fit of any real tire.
SYNTHETIC_TIR = """[UNITS]
LENGTH = 'meter'
FORCE = 'newton'
ANGLE = 'radians'
[VERTICAL]
FNOMIN = 800
[LONG_SLIP_RANGE]
KPUMIN = -0.2
KPUMAX = 0.2
[SLIP_ANGLE_RANGE]
ALPMIN = -0.2
ALPMAX = 0.2
[INCLINATION_ANGLE_RANGE]
CAMMIN = -0.05
CAMMAX = 0.05
[VERTICAL_FORCE_RANGE]
FZMIN = 100
FZMAX = 2000
[LONGITUDINAL_COEFFICIENTS]
PCX1 = 1.6
PDX1 = 1.5
PDX2 = -0.1
PKX1 = 25
[LATERAL_COEFFICIENTS]
PCY1 = 1.4
PDY1 = 1.5
PDY2 = -0.15
PKY1 = -35
PKY2 = 2
"""


def write_tir(directory, text=SYNTHETIC_TIR, name="synthetic.tir"):
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def synthetic_tir(tmp_path):
    return write_tir(tmp_path)


@pytest.fixture
def edited_tir(tmp_path):
    def edit(transform):
        return write_tir(tmp_path, transform(SYNTHETIC_TIR), name="edited.tir")

    return edit


@pytest.fixture(scope="session")
def study():
    spec = importlib.util.spec_from_file_location("front_ackermann_run", SIM / "studies/front-ackermann/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def synthetic_tire(tmp_path_factory):
    from common.vehicle_io import parse_tir

    from tools.tire_support import TireSupport

    path = write_tir(tmp_path_factory.mktemp("tire"))
    return parse_tir(path), TireSupport.from_tir(path)


@pytest.fixture(scope="session")
def make_ggv():
    def make(**overrides):
        values = {
            "mass": 250.0, "wheelbase": 1.55, "cg_height": 0.28, "track_front": 1.25, "track_rear": 1.2,
            "front_static_frac": 0.46, "lltd": 0.5, "max_drive_force": 3000.0,
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    return make


@pytest.fixture(scope="session")
def make_car(study, synthetic_tire, make_ggv):
    tire, support = synthetic_tire

    def make(lltd=0.5, tire_overrides=None, support_override=None, **ggv):
        t = {**tire, **(tire_overrides or {})}
        return study.Car(make_ggv(**ggv), (t,) * 4, lltd, support_override or support)

    return make
