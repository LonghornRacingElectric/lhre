import csv

import yaml

from tools.four_post_sweep import BOBLIB_PACKAGE, BOBSIM, copy_vehicle, write_summary


def test_copy_vehicle_keeps_tires_and_writes_boblib_inside_the_copy(tmp_path):
    car = tmp_path / "car.yml"
    car.write_text(yaml.safe_dump({"paths": {"boblib": "/elsewhere/BobLib", "tire_templates": "../../vehicle/tires"}}))
    workspace = tmp_path / "bobsim"
    workspace.mkdir()
    copy_vehicle(car, workspace)
    paths = yaml.safe_load((workspace / "vehicle.yml").read_text())["paths"]
    assert paths["boblib"] == BOBLIB_PACKAGE
    assert paths["tire_templates"] == str((BOBSIM / "../../vehicle/tires").resolve())


def test_summary_has_one_row_per_car_and_every_metric(tmp_path):
    path = tmp_path / "summary.csv"
    write_summary([{"car": "a", "x": 1}, {"car": "b", "error": "failed"}], path)
    with path.open() as f:
        rows = list(csv.DictReader(f))
    assert [row["car"] for row in rows] == ["a", "b"]
    assert list(rows[0]) == ["car", "x", "error"]
