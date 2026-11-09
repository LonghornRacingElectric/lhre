"""Fixtures: the firmware library and the FMU come from the environment.

run.sh --test sets VCU_SIL_LIB to the library it just built, and VCU_SIL_FMU
when it was given an FMU. Tests that need one skip without it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vcusil import firmware  # noqa: E402


@pytest.fixture(scope='session')
def lib():
    path = os.environ.get('VCU_SIL_LIB')
    if not path:
        pytest.skip('VCU_SIL_LIB is not set (run.sh --test sets it)')
    return firmware.load_library(path)


@pytest.fixture(scope='session')
def fmu_path() -> Path:
    path = os.environ.get('VCU_SIL_FMU')
    if not path:
        pytest.skip('VCU_SIL_FMU is not set (pass --fmu to run.sh --test)')
    return Path(path)
