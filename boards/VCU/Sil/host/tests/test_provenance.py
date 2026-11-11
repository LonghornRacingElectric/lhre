import json
import subprocess

import pytest

from vcusil import provenance


def write_manifest(path, **fields):
    path.write_text(json.dumps({'schema': provenance.MANIFEST_SCHEMA, **fields}))


def test_an_fmu_must_match_its_manifest(tmp_path):
    fmu = tmp_path / 'car.fmu'
    fmu.write_bytes(b'car')
    manifest = tmp_path / 'provenance.json'
    with pytest.raises(provenance.ProvenanceError, match='no provenance.json'):
        provenance.fmu_provenance(fmu)
    write_manifest(manifest, fmu_sha256='0' * 64)
    with pytest.raises(provenance.ProvenanceError, match='sha256'):
        provenance.fmu_provenance(fmu)
    info = provenance.fmu_provenance(fmu, allow_unverified=True)
    assert info['verified'] is False and info['problems']
    write_manifest(manifest, fmu_sha256=provenance.sha256(fmu))
    assert provenance.fmu_provenance(fmu)['verified'] is True
    manifest.write_text(json.dumps({'schema': 'other', 'fmu_sha256': provenance.sha256(fmu)}))
    with pytest.raises(provenance.ProvenanceError, match='schema'):
        provenance.fmu_provenance(fmu)


@pytest.mark.parametrize('text', ['{not json', '[1, 2]'])
def test_a_malformed_manifest_is_a_provenance_error(tmp_path, text):
    fmu = tmp_path / 'car.fmu'
    fmu.write_bytes(b'car')
    (tmp_path / 'provenance.json').write_text(text)
    with pytest.raises(provenance.ProvenanceError):
        provenance.fmu_provenance(fmu, allow_unverified=True)


def test_repo_state_compares_the_car_files_with_the_manifest(tmp_path):
    vehicle = tmp_path / 'vehicle'
    (vehicle / 'tires').mkdir(parents=True)
    (vehicle / 'vehicle.yml').write_text('mass: 1\n')
    (vehicle / 'tires' / 'a.tir').write_text('tire\n')
    good = {'vehicle_sha256': provenance.sha256(vehicle / 'vehicle.yml'),
            'tires': {'tires/a.tir': provenance.sha256(vehicle / 'tires' / 'a.tir')}}
    state = provenance.repo_state(tmp_path, good)
    assert state['vehicle_matches_fmu'] is True and state['tires_match_fmu'] is True
    assert provenance.repo_state(tmp_path, {**good, 'vehicle_sha256': '0' * 64})[
        'vehicle_matches_fmu'] is False
    for tires in ({'tires/a.tir': '0' * 64}, {'tires/missing.tir': '0' * 64},
                  {'../outside.tir': '0' * 64}):
        assert provenance.repo_state(tmp_path, {**good, 'tires': tires})['tires_match_fmu'] is False
    assert provenance.repo_state(tmp_path, {'vehicle_sha256': 'x'})['tires_match_fmu'] is None


def test_repo_state_reads_git(tmp_path):
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init', '-q')
    git('config', 'user.email', 'sil@example.com')
    git('config', 'user.name', 'sil')
    (tmp_path / 'f.txt').write_text('a\n')
    git('add', 'f.txt')
    git('commit', '-qm', 'one')
    clean = provenance.repo_state(tmp_path, None)
    assert len(clean['git_head']) == 40 and clean['git_status'] == []
    (tmp_path / 'f.txt').write_text('b\n')
    dirty = provenance.repo_state(tmp_path, None)
    assert dirty['git_status'] == [' M f.txt']
    assert dirty['git_diff_sha256'] != clean['git_diff_sha256']
    assert 'vehicle_matches_fmu' not in dirty  # no manifest, no verdict
