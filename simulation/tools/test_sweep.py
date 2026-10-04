import csv

import yaml

from tools.sweep import BOBLIB_PACKAGE, BOBSIM, SIMS, build_files, copy_vehicle, read_metrics, tree_digest, write_summary


def test_copy_vehicle_keeps_tires_and_writes_boblib_inside_the_copy(tmp_path):
    car = tmp_path / "car.yml"
    car.write_text(yaml.safe_dump({"paths": {"boblib": "/elsewhere/BobLib", "tire_templates": "../../vehicle/tires"}}))
    workspace = tmp_path / "bobsim"
    workspace.mkdir()
    copy_vehicle(car, workspace)
    paths = yaml.safe_load((workspace / "vehicle.yml").read_text())["paths"]
    assert paths["boblib"] == BOBLIB_PACKAGE
    assert paths["tire_templates"] == str((BOBSIM / "../../vehicle/tires").resolve())


def test_tree_digest_changes_only_when_a_file_changes(tmp_path):
    (tmp_path / "a.mo").write_text("x")
    first = tree_digest(tmp_path)
    assert tree_digest(tmp_path) == first
    (tmp_path / "a.mo").write_text("y")
    assert tree_digest(tmp_path) != first


def test_build_files_keep_only_what_the_executable_needs(tmp_path):
    names = ["BobLib.Experiments.Standards.FourPostSim", "BobLib.Experiments.Standards.FourPostSim_init.xml",
             "BobLib.Experiments.Standards.FourPostSim_info.json", "BobLib.Experiments.Standards.FourPostSim_JacLSJac3.bin",
             "BobLib.Experiments.Standards.FourPostSim.c", "BobLib.Experiments.Standards.FourPostSim.o"]
    for name in names:
        (tmp_path / name).write_text("")
    kept = sorted(path.name for path in build_files(tmp_path, SIMS["four-post"]))
    assert kept == sorted(names[:4])


def test_metrics_keep_groups_and_repeated_names(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text("group,metric,value\nstep,ay_peak,1\nstep,ay_peak,2\n,roll,3\n")
    assert read_metrics(path) == {"step.ay_peak": "1", "step.ay_peak#2": "2", "roll": "3"}


def test_summary_has_one_row_per_car_and_every_metric(tmp_path):
    path = tmp_path / "summary.csv"
    write_summary([{"car": "a", "x": 1}, {"car": "b", "error": "failed"}], path)
    with path.open() as f:
        rows = list(csv.DictReader(f))
    assert [row["car"] for row in rows] == ["a", "b"]
    assert list(rows[0]) == ["car", "x", "error"]
