"""What a run was built from, so a result can be trusted (or not) later."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path

MANIFEST_SCHEMA = 'lhre.fmu.v1'


class ProvenanceError(RuntimeError):
    """The FMU does not match its own provenance manifest."""


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def fmu_provenance(fmu_path: Path, allow_unverified: bool = False) -> dict[str, object]:
    """Checks the FMU against the provenance.json written beside it by its build.

    Without a matching manifest nobody can say which BobLib, BobSim and
    vehicle.yml the car came from, so that is refused unless asked for.
    """
    digest = sha256(fmu_path)
    manifest_path = fmu_path.with_name('provenance.json')
    manifest = None
    problems = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError) as exc:
            raise ProvenanceError(f'{manifest_path} is not readable JSON: {exc}') from exc
        if not isinstance(manifest, dict):
            raise ProvenanceError(f'{manifest_path} is not a JSON object')
    if manifest is None:
        problems.append(f'no {manifest_path.name} beside the FMU')
    else:
        if manifest.get('schema') != MANIFEST_SCHEMA:
            problems.append(f'manifest schema {manifest.get("schema")!r}, want {MANIFEST_SCHEMA!r}')
        if manifest.get('fmu_sha256') != digest:
            problems.append(f'FMU sha256 {digest} != manifest {manifest.get("fmu_sha256")}')
    if problems and not allow_unverified:
        raise ProvenanceError('; '.join(problems))
    return {'fmu': str(fmu_path), 'fmu_sha256': digest, 'manifest': manifest,
            'verified': not problems, 'problems': problems}


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ['git', '-c', f'safe.directory={repo}', '-C', str(repo), *args],
            check=True, capture_output=True, text=True, errors='replace').stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def repo_state(repo: Path, manifest: dict | None) -> dict[str, object]:
    """The checkout the firmware came from, and whether the FMU's car is it.

    The FMU is built from vehicle.yml and a BobSim pin; if either has moved
    since, the model is not the car on this commit.
    """
    head = _git(repo, 'rev-parse', 'HEAD')
    status = _git(repo, 'status', '--porcelain=v1', '--untracked-files=all')
    diff = _git(repo, 'diff', 'HEAD', '--binary')
    pin = _git(repo, 'ls-tree', 'HEAD', 'simulation/bobsim')
    vehicle = repo / 'vehicle' / 'vehicle.yml'
    state: dict[str, object] = {
        'git_head': head.strip() if head else None,
        'git_status': status.splitlines() if status is not None else None,
        'git_diff_sha256': hashlib.sha256(diff.encode()).hexdigest() if diff is not None else None,
        'vehicle_yml_sha256': sha256(vehicle) if vehicle.exists() else None,
        'bobsim_pin': pin.split()[2] if pin and len(pin.split()) >= 3 else None,
    }
    if manifest:
        state['vehicle_matches_fmu'] = state['vehicle_yml_sha256'] == manifest.get('vehicle_sha256')
        state['bobsim_pin_matches_fmu'] = state['bobsim_pin'] == manifest.get('bobsim_sha')
        state['tires_match_fmu'] = _tires_match(vehicle.parent, manifest.get('tires'))
    return state


def _tires_match(vehicle_dir: Path, tires: object) -> bool | None:
    """Whether every tire file the FMU was built from is unchanged here."""
    if not isinstance(tires, dict) or not tires:
        return None
    root = vehicle_dir.resolve()
    for relative, digest in tires.items():
        path = (vehicle_dir / str(relative)).resolve()
        if root not in path.parents or not path.is_file() or sha256(path) != digest:
            return False
    return True


def host_info() -> dict[str, object]:
    import numpy

    from ._fmpy import fmpy
    return {
        'machine': platform.machine(),
        'python': platform.python_version(),
        'fmpy': fmpy.__version__,
        'numpy': numpy.__version__,
    }
