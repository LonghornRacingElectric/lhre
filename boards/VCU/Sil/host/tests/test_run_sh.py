"""run.sh with a stub docker and df first on PATH: what it hands each
container, and what it leaves in the run directory. No container, firmware
or FMU runs, so these need neither Docker nor a build."""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from vcusil import scenario_file

SIL = Path(__file__).resolve().parents[2]
RUN_SH = SIL / 'run.sh'
REPO = SIL.parents[2]  # physical, as run.sh's pwd -P makes it
LAUNCH_IN_CONTAINER = '/work/lhre/boards/VCU/Sil/scenarios/launch.toml'
OLD = 946684800  # 2000-01-01 UTC: a previous run's meta.json
STAMP = 1577836800  # 2020-01-01 UTC: run.sh's stamp, from the mktemp stub
NOW = '20260102-030405'  # what the date stub says
UID = os.getuid()

# Logs every call. A docker run with a /out mount stands in for vcusil:
# STUB_META=new writes a meta.json dated a second after run.sh's stamp, =old
# one dated before it; STUB_EXIT is its status. No time here is the clock's.
STUB_DOCKER = f'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['STUB_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps(args) + '\\n')
if args[:1] != ['run'] or '-d' in args:
    sys.exit(0)  # info, build, rm and the viewer
outs = [args[i + 1][:-5] for i, a in enumerate(args) if a == '-v' and args[i + 1].endswith(':/out')]
meta = os.environ.get('STUB_META')
if outs and meta:
    path = Path(outs[0]) / 'meta.json'
    path.write_text('{{"run": "this one"}}', encoding='utf-8')
    when = {STAMP} + 1 if meta == 'new' else {OLD}
    os.utime(path, (when, when))
sys.exit(int(os.environ.get('STUB_EXIT', '0')))
'''

STUB_DF = '''#!/bin/sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'
printf 'stub 1 1 %s 1%% /\\n' "${STUB_FREE_KB:-999999999}"
'''

# run.sh's stamp, at a known path and time. 'x': a stamp left by an earlier
# run (its trap failed) stops the next one.
STUB_MKTEMP = f'''#!/usr/bin/env python3
import os
path = os.environ['STUB_STAMP']
open(path, 'x').close()
os.utime(path, ({STAMP}, {STAMP}))
print(path)
'''

# Only the format run.sh names a run directory with.
STUB_DATE = f'''#!/bin/sh
[ "$*" = '+%Y%m%d-%H%M%S' ] || {{ echo "date stub: unexpected arguments: $*" >&2; exit 1; }}
echo {NOW}
'''


@dataclass
class Result:
    code: int
    out: str
    err: str
    calls: list

    def runs(self) -> list:
        return [c for c in self.calls if c[0] == 'run']

    def run(self) -> list:
        [call] = self.runs()
        return call


def volumes(call: list) -> list:
    return [call[i + 1] for i, a in enumerate(call) if a == '-v']


def handed(call: list) -> list:
    """What follows $0 (run.sh) in the container's bash -c."""
    i = call.index('-c')
    assert call[i + 2] == 'run.sh'
    return call[i + 3:]


class RunSh:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / 'bin'
        self.bin.mkdir()
        for name, text in (('docker', STUB_DOCKER), ('df', STUB_DF), ('mktemp', STUB_MKTEMP),
                           ('date', STUB_DATE)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.log = tmp / 'docker.log'
        self.stamp = tmp / 'stamp'
        self.fmu = tmp / 'fmus' / 'car.fmu'
        self.fmu.parent.mkdir()
        self.fmu.write_bytes(b'')

    def __call__(self, *args, script: Path = RUN_SH, cwd: Path | None = None,
                 **env: str) -> Result:
        self.log.write_text('')
        # Only what run.sh needs: no VCU_SIL_FMU from the session running this.
        environ = {'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                   'HOME': str(self.tmp), 'TMPDIR': str(self.tmp),
                   'STUB_LOG': str(self.log), 'STUB_STAMP': str(self.stamp), **env}
        p = subprocess.run(['bash', str(script), *map(str, args)], cwd=cwd or self.tmp,
                           env=environ, capture_output=True, text=True, check=False,
                           timeout=60)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return Result(p.returncode, p.stdout, p.stderr, calls)


@pytest.fixture
def sh(tmp_path):
    return RunSh(tmp_path)


def test_help_is_the_header_comment(sh):
    r = sh('--help')
    lines = r.out.splitlines()
    assert r.code == 0 and r.calls == []
    assert lines[0] == 'Runs the VCU firmware against the car model in the SIL dev container.'
    assert '  boards/VCU/Sil/run.sh --check [--strict] [--explain] [--errors-only]' in lines
    assert lines[-1] == 'leaves no run directory, and a reused --out as it was.'


def test_a_value_may_follow_its_option_after_an_equals(sh):
    out = sh.tmp / 'runs' / 'a=b'  # only the first = splits
    spaced = sh('--fmu', sh.fmu, '--out', out, '--scenario', 'part-throttle', '--no-plot')
    joined = sh(f'--fmu={sh.fmu}', f'--out={out}', '--scenario=part-throttle', '--no-plot')
    assert spaced.code == joined.code == 0
    assert joined.run() == spaced.run()
    assert f'{sh.fmu.parent}:/fmu:ro' in volumes(joined.run())
    assert f'{out}:/out' in volumes(joined.run())
    assert handed(joined.run()) == ['/fmu/car.fmu', 'part-throttle', '--no-plot']


@pytest.mark.parametrize('args, message', [
    (['--fmu='], '--fmu needs a path'),
    (['--out='], '--out needs a path'),
    (['--scenario='], '--scenario needs a name or a .toml file'),
    (['--view='], '--view needs a run directory'),
    (['--port='], '--port needs a number'),
    (['--check', '--scenario'], '--scenario needs a name or a .toml file'),
    (['--out', ''], '--out needs a path'),
])
def test_an_option_without_its_value_stops_before_docker(sh, args, message):
    r = sh(*args)
    assert (r.code, r.err, r.calls) == (2, f'run.sh: {message}\n', [])


def _inside(sh):
    return {
        'name': ('launch', None, 'launch'),
        'absolute': (SIL / 'scenarios' / 'launch.toml', None, LAUNCH_IN_CONTAINER),
        'relative': ('scenarios/launch.toml', SIL, LAUNCH_IN_CONTAINER),
        'symlinked-checkout': (sh.tmp / 'checkout' / 'boards/VCU/Sil/scenarios/launch.toml',
                               None, LAUNCH_IN_CONTAINER),
        'symlinked-folder': (sh.tmp / 'alias' / 'launch.toml', None, LAUNCH_IN_CONTAINER),
    }


@pytest.mark.parametrize('spelling', ['name', 'absolute', 'relative', 'symlinked-checkout',
                                      'symlinked-folder'])
def test_check_hands_a_name_as_is_and_a_file_as_its_path_under_work_lhre(sh, spelling):
    (sh.tmp / 'checkout').symlink_to(REPO, target_is_directory=True)
    (sh.tmp / 'alias').symlink_to(SIL / 'scenarios', target_is_directory=True)
    arg, cwd, expected = _inside(sh)[spelling]
    r = sh('--check', arg, cwd=cwd)
    assert r.code == 0, r.err
    assert handed(r.run()) == [expected]


def test_through_a_symlinked_checkout_the_real_one_is_mounted(sh):
    (sh.tmp / 'checkout').symlink_to(REPO, target_is_directory=True)
    sil = sh.tmp / 'checkout' / 'boards/VCU/Sil'
    r = sh('--check', 'scenarios/launch.toml', script=sil / 'run.sh', cwd=sil)
    assert r.code == 0, r.err
    assert volumes(r.run()) == [f'{REPO}:/work/lhre:ro']
    assert handed(r.run()) == [LAUNCH_IN_CONTAINER]
    r = sh('--fmu', sh.fmu, '--out', sh.tmp / 'run', script=sil / 'run.sh')
    assert r.code == 0, r.err
    assert volumes(r.run())[0] == f'{REPO}:/work/lhre:ro'


def _unseen(sh):
    (sh.tmp / 'mine.toml').write_text((SIL / 'scenarios' / 'launch.toml').read_text())
    (sh.tmp / 'link.toml').symlink_to(SIL / 'scenarios' / 'launch.toml')
    outside = ('is outside this checkout, the only folder the SIL container sees; move it '
               'under boards/VCU/Sil/scenarios/')
    return {
        'outside': (sh.tmp / 'mine.toml', outside),
        # The link is outside even though its target is not: the container
        # would see neither the link nor that path.
        'link-from-outside': (sh.tmp / 'link.toml', outside),
        'missing': (sh.tmp / 'gone.toml', None),
        'toml-name-not-here': ('launch.toml', None),
    }


@pytest.mark.parametrize('case', ['outside', 'link-from-outside', 'missing',
                                  'toml-name-not-here'])
def test_a_file_the_container_cannot_see_stops_before_any_container(sh, case):
    arg, outside = _unseen(sh)[case]
    message = f'run.sh: {arg} {outside}\n' if outside else f'run.sh: no scenario file at {arg}\n'
    r = sh('--check', 'launch', arg)
    assert (r.code, r.err, r.runs()) == (2, message, [])
    out = sh.tmp / 'runs' / 'new'
    r = sh('--fmu', sh.fmu, '--out', out, '--scenario', arg)
    assert (r.code, r.err, r.runs()) == (2, message, [])
    assert not out.exists() and not out.parent.exists()


def test_check_passes_options_then_each_scenario_option(sh):
    r = sh('--check', '--strict', 'launch', '--scenario', 'part-throttle', '--explain',
           '--scenario=brake-over-throttle')
    assert r.code == 0, r.err
    assert handed(r.run()) == ['--strict', 'launch', '--explain', 'part-throttle',
                               'brake-over-throttle']
    # The check container gets the checkout read-only and nothing else.
    assert r.run()[:5] == ['run', '--rm', '-v', f'{REPO}:/work/lhre:ro', 'lhre-vcu-sil:local']
    assert handed(sh('--check').run()) == []


def test_check_needs_no_fmu_and_no_room_for_a_build(sh):
    gone = str(sh.tmp / 'gone.fmu')
    r = sh('--check', VCU_SIL_FMU=gone, STUB_FREE_KB='1')
    assert (r.code, len(r.runs())) == (0, 1)
    r = sh('--fmu', sh.fmu, STUB_FREE_KB='1')
    assert (r.code, r.runs()) == (2, [])
    assert r.err.startswith('run.sh: less than 10 GB free')
    assert sh(VCU_SIL_FMU=gone).err == f'run.sh: no FMU at {gone}\n'
    assert sh().err == 'run.sh: --fmu PATH (or VCU_SIL_FMU) is required\n'


@pytest.mark.parametrize('args, expected', [
    ([], 'launch'),
    (['--scenario', 'launch', '--scenario', 'part-throttle'], 'part-throttle'),
    (['--scenario', SIL / 'scenarios' / 'launch.toml'], LAUNCH_IN_CONTAINER),
])
def test_a_run_drives_the_last_scenario_option(sh, args, expected):
    r = sh('--fmu', sh.fmu, '--out', sh.tmp / 'run', *args)
    assert r.code == 0, r.err
    assert handed(r.run()) == ['/fmu/car.fmu', expected]


def test_a_run_hands_the_container_the_checkout_caches_fmu_and_run_directory(sh):
    out = sh.tmp / 'run'
    r = sh('--fmu', sh.fmu, '--out', out)
    assert volumes(r.run()) == [f'{REPO}:/work/lhre:ro',
                                f'lhre-sil-bazel-{UID}:/home/dev/.cache/bazel',
                                f'lhre-sil-bazelisk-{UID}:/home/dev/.cache/bazelisk',
                                f'{sh.fmu.parent}:/fmu:ro', f'{out}:/out']


# (directory before, what the run writes, its status) -> (run.sh's status,
# whether it says it wrote the run, whether the directory is still there)
RUN_DIRECTORIES = {
    'new-and-completed': ('none', 'new', '0', 0, True, True),
    'new-and-the-car-model-failed': ('none', 'new', '1', 1, True, True),
    # A directory run.sh made holds only this run, whatever the file clock.
    'new-with-a-meta-older-than-the-stamp': ('none', 'old', '0', 0, True, True),
    'new-and-stopped-before-it-started': ('none', None, '2', 2, False, False),
    'new-and-docker-failed': ('none', None, '125', 2, False, False),
    'reused-and-stopped-before-it-started': ('previous', None, '2', 2, False, True),
    'reused-and-completed': ('previous', 'new', '0', 0, True, True),
    'reused-empty-and-stopped-before-it-started': ('empty', None, '2', 2, False, True),
}


@pytest.mark.parametrize('case', RUN_DIRECTORIES)
def test_what_a_run_leaves_in_its_directory(sh, case):
    before, meta, status, code, wrote, kept = RUN_DIRECTORIES[case]
    out = sh.tmp / 'run'
    if before != 'none':
        out.mkdir()
    if before == 'previous':
        (out / 'meta.json').write_text('{"run": "the previous one"}')
        os.utime(out / 'meta.json', (OLD, OLD))
    env = {'STUB_EXIT': status, **({'STUB_META': meta} if meta else {})}
    r = sh('--fmu', sh.fmu, '--out', out, **env)
    assert r.code == code
    line = f'  wrote {out} (open it with: {RUN_SH} --view {out})\n'
    assert r.out == (line if wrote else '')
    assert out.is_dir() == kept
    assert not sh.stamp.exists()  # run.sh's trap removes it
    if before == 'previous' and not wrote:
        assert (out / 'meta.json').read_text() == '{"run": "the previous one"}'
        assert (out / 'meta.json').stat().st_mtime == OLD


@pytest.mark.parametrize('scenario, stem', [('launch', 'launch'),
                                             ('scenarios/my-drive.toml', 'my-drive')])
def test_a_run_without_out_is_named_for_its_scenario(sh, scenario, stem):
    # A copy in a checkout of its own, so the default run directory under
    # boards/VCU/Sil/out/ is made here rather than in the real one.
    fake = sh.tmp / 'fake'
    sil = fake / 'boards/VCU/Sil'
    (sil / 'scenarios').mkdir(parents=True)
    (sil / 'scenarios' / 'my-drive.toml').write_text('')
    shutil.copy(RUN_SH, sil / 'run.sh')
    r = sh('--fmu', sh.fmu, '--scenario', scenario, script=sil / 'run.sh', cwd=sil,
           STUB_META='new')
    assert r.code == 0, r.err
    [out] = (sil / 'out').iterdir()
    assert out.name == f'{stem}-{NOW}'
    assert f'{out}:/out' in volumes(r.run())
    assert volumes(r.run())[0] == f'{fake}:/work/lhre:ro'
    expected = '/work/lhre/boards/VCU/Sil/scenarios/my-drive.toml' if '/' in scenario else stem
    assert handed(r.run()) == ['/fmu/car.fmu', expected]


def test_an_out_that_cannot_be_a_directory_stops_before_any_container(sh):
    taken = sh.tmp / 'a-file'
    taken.write_text('not a run')
    r = sh('--fmu', sh.fmu, '--out', taken)
    assert (r.code, r.runs()) == (2, [])
    assert r.err.endswith(f'run.sh: cannot make the run directory {taken}\n')
    assert taken.read_text() == 'not a run'


def test_view_takes_its_values_after_an_equals_too(sh):
    run = sh.tmp / 'run'
    run.mkdir()
    (run / 'run.mcap').write_bytes(b'')
    r = sh(f'--view={run}', '--port=18090')
    assert r.code == 0, r.err
    assert [c[:2] for c in r.calls] == [['info'], ['rm', '-f'], ['run', '-d']]
    assert r.calls[1][2] == f'lhre-sil-view-{UID}'
    assert '127.0.0.1:18090:8080' in r.run() and f'{run}:/src/data:ro' in volumes(r.run())
    assert r.out.startswith('Open http://127.0.0.1:18090/?ds=remote-file&ds.url=')


def test_test_mode_hands_pytest_only_its_own_options(sh):
    r = sh('--test')
    assert r.code == 0, r.err
    assert handed(r.run()) == [] and 'VCU_SIL_FMU=' in r.run()
    r = sh('--test', '--fmu', sh.fmu, '-k', 'a b')
    assert handed(r.run()) == ['-k', 'a b'] and 'VCU_SIL_FMU=/fmu/car.fmu' in r.run()


def test_every_shipped_scenario_is_a_name_run_sh_passes_through(sh):
    """Names, not paths, so neither the check nor a run maps them."""
    names = scenario_file.names()
    r = sh('--check', *names)
    assert r.code == 0, r.err
    assert handed(r.run()) == names
