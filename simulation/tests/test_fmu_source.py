import subprocess

import pytest

from tools.build_fmu import require_matching_models


def repository(path, content):
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    (path/'BobLib').mkdir()
    (path/'BobLib/model.mo').write_text(content)
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)


def test_matching_model_trees_are_allowed_without_shared_history(tmp_path):
    source, bobsim = tmp_path/'source', tmp_path/'bobsim'
    repository(source, 'same model')
    repository(bobsim/'engines/boblib', 'same model')
    subprocess.run(['git', 'init', '-q', str(bobsim)], check=True)
    subprocess.run(['git', '-C', str(bobsim), 'add', 'engines/boblib'], check=True)
    subprocess.run(['git', '-C', str(bobsim), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'pin'], check=True)
    require_matching_models(source, bobsim)
    (source/'BobLib/model.mo').write_text('different model')
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '-qam', 'changed'], check=True)
    with pytest.raises(ValueError, match='model trees differ'):
        require_matching_models(source, bobsim)


def test_uncommitted_dependency_changes_cannot_hide_behind_a_matching_tree(tmp_path):
    source, bobsim = tmp_path/'source', tmp_path/'bobsim'
    repository(source, 'same model')
    repository(bobsim, 'same model')
    repository(bobsim/'engines/boblib', 'same model')
    with pytest.raises(ValueError, match='local changes'):
        require_matching_models(source, bobsim)
