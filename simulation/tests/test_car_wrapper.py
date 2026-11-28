import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
NAME = "TestCar"
BOBLIB = "engines/boblib/BobLib"
METRICS = "four_post_eval_report_metrics.csv"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

OUR_VEHICLE = f"""\
schema: boblib.vehicle.v1
vehicle:
  name: {NAME}
paths:
  boblib: {BOBLIB}
  tire_templates: ../../vehicle/tires
sprung_mass:
  mass_kg: 160.0
front:
  tire:
    template: fake_tire
rear:
  tire:
    template: fake_tire
"""
BOBSIM_VEHICLE = OUR_VEHICLE.replace("mass_kg: 160.0", "mass_kg: 999.0")
FOUR_POST_CONFIG = "simulations/response/mbd/four_post/four_post_eval_config.yml"
BUNDLE = """\
schema: lhre.vehicle-bundle.v1
bundle_id: test-car-1
status: candidate
members:
  vehicle:
    path: vehicle/vehicle.yml
    sha256: {sha256}
"""

FAKE_VEHICLE_IO = '''\
from pathlib import Path

import yaml


def repo_root():
    return Path(__file__).resolve().parents[1]


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def require_side(data, side):
    return data[side]


def tire_template_name(data, side):
    return side["tire"]["template"]


def tire_templates_root(data):
    return repo_root() / data["paths"]["tire_templates"]
'''

# Stands in for BobSim's makefile: it reads the car from BOBSIM_VEHICLE and
# writes only under BOBSIM_OUT, as BobSim does. A build is skipped when its
# executable exists, so a test can see that make car keeps the build cache.
FAKE_MAKEFILE = f"""\
RESULTS := $(BOBSIM_OUT)/generated_results
BUILD := $(BOBSIM_OUT)/BuildBobLib/FourPostSim
RUNDIR := $(BUILD)/results/run_new

define write_run
\tmkdir -p $(RESULTS) $(RUNDIR)
\ttest -f $(BUILD)/exe || printf 'build\\n' >> $(BUILD)/builds.log
\ttouch $(BUILD)/exe
\tcp "$(BOBSIM_VEHICLE)" $(RESULTS)/seen_vehicle.yml
\tprintf 'metric,value\\nexample,1.0\\n' > $(RESULTS)/{METRICS}
\tprintf 'fake tire\\n' > $(RESULTS)/leaked.tir
\tprintf '{{}}\\n' > $(RUNDIR)/manifest.json
\tprintf 'log\\n' > $(RUNDIR)/run.log
\tprintf 'time\\n0\\n' > $(RUNDIR)/BobLib.Experiments.Standards.FourPostSim_res.csv
endef

ok:
\t$(write_run)

fail:
\t$(write_run)
\texit 3

dirty:
\t$(write_run)
\tprintf 'changed\\n' >> README.md

hang:
\t$(write_run)
\ttouch $(RESULTS)/started
\tsleep 60

which:
\t@echo "vehicle=[$(BOBSIM_VEHICLE)] out=[$(BOBSIM_OUT)]"
"""

# Stands in for docker compose: runs the command in simulation/ with the
# compose file's environment, then the -e flags, and keeps running through
# Ctrl-C so the tool inside can finish, as a container does.
FAKE_COMPOSE = '''\
import os
import subprocess
import sys
from pathlib import Path

SIM = Path(__file__).resolve().parent / "lhre" / "simulation"
args = sys.argv[1:]
if args[:2] == ["config", "--images"]:
    print("fake-image:test")
    sys.exit(0)
assert args.pop(0) == "run", args
env = dict(os.environ)
env["BOBSIM_VEHICLE"] = str(SIM.parent / "vehicle" / "vehicle.yml")
env["PYTHONPATH"] = f"{SIM / 'bobsim'}:{SIM}"
while args[0].startswith("-"):
    flag = args.pop(0)
    if flag == "-e":
        key, _, value = args.pop(0).partition("=")
        env[key] = value
assert args.pop(0) == "sim", args
process = subprocess.Popen(args, cwd=SIM, env=env)
while True:
    try:
        sys.exit(process.wait())
    except KeyboardInterrupt:
        pass
'''

FAKE_BUNDLE_CHECK = '''\
import hashlib
import sys
from pathlib import Path

import yaml

bundle = Path(sys.argv[sys.argv.index("--bundle") + 1])
vehicle = Path(sys.argv[sys.argv.index("--vehicle") + 1])
expected = yaml.safe_load(bundle.read_text(encoding="utf-8"))["members"]["vehicle"]["sha256"]
if hashlib.sha256(vehicle.read_bytes()).hexdigest() != expected:
    print("FAIL: members.vehicle.sha256 does not match the vehicle.")
    sys.exit(1)
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def lhre(tmp_path):
    root = tmp_path / "lhre"
    _write(root / "vehicle" / "vehicle.yml", OUR_VEHICLE)
    _write(root / "vehicle" / "bundle.yml", BUNDLE.format(sha256=hashlib.sha256(OUR_VEHICLE.encode()).hexdigest()))
    _write(root / "vehicle" / "tires" / "fake_tire.tir", "FAKE TIRE\n")
    sim = root / "simulation"
    for name in ("Makefile", "tools/car.py", "tools/car_identity.py"):
        _write(sim / name, (SIM / name).read_text(encoding="utf-8"))
    _write(sim / "tools" / "bundle.py", FAKE_BUNDLE_CHECK)
    bobsim = sim / "bobsim"
    _write(bobsim / "vehicle.yml", BOBSIM_VEHICLE)
    _write(bobsim / "README.md", "fake BobSim\n")
    _write(bobsim / "makefile", FAKE_MAKEFILE)
    _write(bobsim / ".gitignore", "__pycache__/\n")
    _write(bobsim / "common" / "__init__.py", "")
    _write(bobsim / "common" / "vehicle_io.py", FAKE_VEHICLE_IO)
    _write(bobsim / FOUR_POST_CONFIG, "procedure:\n  heaveMagnitude: 0.03\n")
    _git(bobsim, "init", "-q")
    _git(bobsim, "add", "-A")
    _git(bobsim, "commit", "-q", "-m", "fake BobSim")
    _git(root, "init", "-q")
    _write(root / ".gitignore", "simulation/bobsim/\nsimulation/out/\n__pycache__/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fake lhre")
    _write(tmp_path / "compose.py", FAKE_COMPOSE)
    return root


def _compose(lhre):
    return f"{sys.executable} {lhre.parent / 'compose.py'}"


def _make(lhre, *args):
    return subprocess.run(  # noqa: PLW1510 the tests read returncode
        ["make", *args, f"COMPOSE={_compose(lhre)}"],
        cwd=lhre / "simulation",
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )


def _make_car(lhre, target, *args):
    return _make(lhre, "car", f"T={target}", *args)


def _bobsim_status(lhre):
    return subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules=none"],
        cwd=lhre / "simulation" / "bobsim",
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _out(lhre, target):
    return lhre / "simulation" / "out" / "car" / target


def _identity(lhre, target):
    return json.loads((_out(lhre, target) / "identity.json").read_text(encoding="utf-8"))


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_make_car_hands_bobsim_our_vehicle_and_leaves_bobsim_untouched(lhre):
    result = _make_car(lhre, "ok")
    assert result.returncode == 0, result.stdout + result.stderr

    bobsim = lhre / "simulation" / "bobsim"
    out = _out(lhre, "ok")
    our_vehicle = lhre / "vehicle" / "vehicle.yml"
    assert (out / "generated_results" / "seen_vehicle.yml").read_bytes() == our_vehicle.read_bytes()
    assert (bobsim / "vehicle.yml").read_text(encoding="utf-8") == BOBSIM_VEHICLE
    assert _bobsim_status(lhre) == ""

    assert (out / "vehicle.yml").read_bytes() == our_vehicle.read_bytes()
    assert (out / "generated_results" / METRICS).is_file()
    run = out / "runs" / "FourPostSim" / "run_new"
    assert {p.name for p in run.iterdir()} == {
        "manifest.json",
        "run.log",
        "BobLib.Experiments.Standards.FourPostSim_res.csv",
    }
    assert not list((out / "generated_results").glob("*.tir")), "make car copied a .tir file into the outputs"
    assert (out / "target.log").is_file()

    identity = _identity(lhre, "ok")
    assert identity["schema"] == "lhre.car-run.v2"
    assert "records" not in identity
    assert identity["status"] == "ok", identity
    assert identity["failures"] == []
    assert identity["target"] == "ok"
    assert identity["target_exit_status"] == 0
    assert identity["vehicle"]["name"] == NAME
    assert identity["vehicle"]["sha256"] == _sha256(our_vehicle)
    assert identity["vehicle"]["executed_sha256"] == _sha256(our_vehicle)
    assert identity["vehicle"]["candidate"] is False
    assert identity["bobsim"]["commit"] and identity["bobsim"]["clean_after"] is True
    assert identity["lhre"]["commit"] and identity["lhre"]["dirty"] is False
    assert identity["image"]["name"] == "fake-image:test"
    assert identity["tires"] == [
        {"template": "fake_tire", "axles": ["front", "rear"], "sha256": _sha256(lhre / "vehicle/tires/fake_tire.tir")}
    ]
    assert identity["bundle"] == {
        "path": "vehicle/bundle.yml",
        "sha256": _sha256(lhre / "vehicle" / "bundle.yml"),
        "bundle_id": "test-car-1",
        "status": "candidate",
        "describes_vehicle": True,
    }
    outputs = {item["path"]: item["sha256"] for item in identity["outputs"]}
    assert outputs[f"generated_results/{METRICS}"] == _sha256(out / "generated_results" / METRICS)
    assert outputs["four_post_eval_config.yml"] == _sha256(bobsim / FOUR_POST_CONFIG)
    assert not [path for path in outputs if path.startswith(".bobsim/")], "the build cache is not an output"


def test_make_car_keeps_the_build_and_clears_the_last_results(lhre):
    assert _make_car(lhre, "ok").returncode == 0
    cache = _out(lhre, "ok") / ".bobsim"
    _write(cache / "BuildBobLib/FourPostSim/results/run_old/run.log", "an older run\n")
    _write(cache / "generated_results/old_car_metrics.csv", "metric,value\nold,1\n")
    _write(_out(lhre, "ok") / "left_over.txt", "from an older run\n")

    result = _make_car(lhre, "ok")
    assert result.returncode == 0, result.stdout + result.stderr
    out = _out(lhre, "ok")
    assert (cache / "BuildBobLib/FourPostSim/builds.log").read_text(encoding="utf-8") == "build\n"
    assert {p.name for p in (out / "runs" / "FourPostSim").iterdir()} == {"run_new"}
    assert not (out / "generated_results" / "old_car_metrics.csv").exists()
    assert not (out / "left_over.txt").exists()


def test_make_car_leaves_a_run_folder_the_host_can_delete(lhre):
    assert _make_car(lhre, "ok").returncode == 0
    out = _out(lhre, "ok")
    for path in [out, *out.rglob("*")]:
        mode = path.stat().st_mode
        assert mode & stat.S_IWOTH, f"{path} is not writable by everyone"


def _candidate(lhre, bundle_sha256=None):
    vehicle = OUR_VEHICLE.replace("mass_kg: 160.0", "mass_kg: 170.0")
    folder = lhre / "simulation" / "out" / "cand"
    _write(folder / "vehicle.yml", vehicle)
    _write(folder / "bundle.yml", BUNDLE.format(sha256=bundle_sha256 or hashlib.sha256(vehicle.encode()).hexdigest()))
    return folder / "vehicle.yml"


def test_make_car_runs_a_candidate_vehicle_into_its_own_folder(lhre):
    assert _make_car(lhre, "ok").returncode == 0
    candidate = _candidate(lhre)
    result = _make_car(lhre, "ok", "VEHICLE=out/cand/vehicle.yml")
    assert result.returncode == 0, result.stdout + result.stderr

    assert _bobsim_status(lhre) == ""
    assert (_out(lhre, "ok--cand") / "generated_results" / "seen_vehicle.yml").read_bytes() == candidate.read_bytes()
    assert (_out(lhre, "ok--cand") / "vehicle.yml").read_bytes() == candidate.read_bytes()
    assert (_out(lhre, "ok") / "vehicle.yml").read_bytes() == (lhre / "vehicle" / "vehicle.yml").read_bytes()
    identity = _identity(lhre, "ok--cand")
    assert identity["status"] == "ok", identity
    assert identity["vehicle"]["path"] == "simulation/out/cand/vehicle.yml"
    assert identity["vehicle"]["sha256"] == _sha256(candidate)
    assert identity["vehicle"]["candidate"] is True
    assert identity["bundle"]["path"] == "simulation/out/cand/bundle.yml"
    assert identity["bundle"]["describes_vehicle"] is True
    assert "is not the vehicle member" in result.stdout
    assert _identity(lhre, "ok")["vehicle"]["candidate"] is False


def test_make_car_stops_before_the_target_on_a_candidate_that_fails_the_check(lhre):
    _candidate(lhre, bundle_sha256="0" * 64)
    result = _make_car(lhre, "ok", "VEHICLE=out/cand/vehicle.yml")
    assert result.returncode != 0
    assert "fails the candidate check" in result.stderr
    assert not _out(lhre, "ok--cand").exists()
    assert _bobsim_status(lhre) == ""


def test_make_car_fails_without_a_vehicle_bundle(lhre):
    (lhre / "vehicle" / "bundle.yml").unlink()
    _git(lhre, "commit", "-q", "-am", "no bundle")
    result = _make_car(lhre, "ok")
    assert result.returncode != 0
    assert "bundle" in result.stdout + result.stderr
    identity = _identity(lhre, "ok")
    assert identity["status"] == "failed"
    assert identity["bundle"] is None
    assert _bobsim_status(lhre) == ""


def test_make_car_records_a_bundle_that_does_not_describe_the_vehicle(lhre):
    (lhre / "vehicle" / "bundle.yml").write_text(BUNDLE.format(sha256="0" * 64), encoding="utf-8")
    _git(lhre, "commit", "-q", "-am", "stale bundle")
    result = _make_car(lhre, "ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not describe" in result.stdout
    assert _identity(lhre, "ok")["bundle"]["describes_vehicle"] is False


def test_make_car_marks_a_dirty_lhre_tree(lhre):
    (lhre / "simulation" / "Makefile").write_text(
        (lhre / "simulation" / "Makefile").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    assert _make_car(lhre, "ok").returncode == 0
    assert _identity(lhre, "ok")["lhre"]["dirty"] is True


def test_make_car_keeps_the_outputs_of_a_failing_target(lhre):
    result = _make_car(lhre, "fail")
    assert result.returncode != 0
    assert "make -C bobsim fail failed with exit status 2." in result.stdout
    assert _bobsim_status(lhre) == ""
    identity = _identity(lhre, "fail")
    assert identity["status"] == "failed"
    assert identity["target_exit_status"] == 2
    assert identity["bobsim"]["clean_after"] is True
    assert (_out(lhre, "fail") / "runs/FourPostSim/run_new/run.log").is_file()


def test_make_car_fails_when_the_target_leaves_bobsim_changed(lhre):
    result = _make_car(lhre, "dirty")
    assert result.returncode != 0
    assert "README.md" in result.stdout + result.stderr
    identity = _identity(lhre, "dirty")
    assert identity["status"] == "failed"
    assert identity["target_exit_status"] == 0
    assert identity["bobsim"]["clean_after"] is False


def test_make_car_writes_a_failed_identity_after_an_interrupt(lhre):
    started = _out(lhre, "hang") / ".bobsim" / "generated_results" / "started"
    process = subprocess.Popen(
        ["make", "car", "T=hang", f"COMPOSE={_compose(lhre)}"],
        cwd=lhre / "simulation",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, **GIT_ENV},
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while not started.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert started.exists(), "The fake target did not start."
    os.killpg(process.pid, signal.SIGINT)
    output, _ = process.communicate(timeout=60)
    assert process.returncode != 0, output
    assert _bobsim_status(lhre) == "", output
    identity = _identity(lhre, "hang")
    assert identity["status"] == "failed"
    assert "make car was interrupted." in identity["failures"]


def test_make_car_refuses_a_bobsim_with_local_changes(lhre):
    bobsim = lhre / "simulation" / "bobsim"
    (bobsim / "README.md").write_text("local edit\n", encoding="utf-8")
    result = _make_car(lhre, "ok")
    assert result.returncode != 0
    assert "local changes" in result.stdout + result.stderr
    assert (bobsim / "README.md").read_text(encoding="utf-8") == "local edit\n"
    assert not _out(lhre, "ok").exists()


def test_make_car_rejects_a_candidate_in_a_nested_folder(lhre):
    _write(lhre / "simulation/out/a/b/vehicle.yml", OUR_VEHICLE)
    result = _make_car(lhre, "ok", "VEHICLE=out/a/b/vehicle.yml")
    assert result.returncode != 0
    assert "is not a file under out/<name>/" in result.stderr
    assert not (lhre / "simulation/out/car").exists()


def test_make_car_rejects_a_missing_candidate(lhre):
    result = _make_car(lhre, "ok", "VEHICLE=out/cand/vehicle.yml")
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert not (lhre / "simulation/out/car").exists()


@pytest.mark.parametrize("target", ["ok VEHICLE=x", "-B", "../ok", "ok--cand"])
def test_make_car_rejects_a_target_that_is_not_a_name(lhre, target):
    result = _make_car(lhre, target)
    assert result.returncode != 0
    assert "not a BobSim target name" in result.stdout + result.stderr
    assert _bobsim_status(lhre) == ""
    assert not (lhre / "simulation" / "out").exists() or not any((lhre / "simulation" / "out").rglob("*"))


def test_make_bobsim_runs_bobsim_on_its_own_car(lhre):
    result = _make(lhre, "bobsim", "T=which")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "vehicle=[] out=[]" in result.stdout


def test_make_car_is_not_run_by_a_dry_run():
    result = subprocess.run(  # noqa: PLW1510 the test reads returncode
        ["make", "-n", "car", "T=dry-run-probe", "COMPOSE=compose"],
        cwd=SIM,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "tools/car.py 'dry-run-probe' '../vehicle/vehicle.yml' 'out/car/dry-run-probe'" in result.stdout
    assert not (SIM / "out" / "car" / "dry-run-probe").exists()
