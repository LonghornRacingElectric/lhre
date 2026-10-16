import dataclasses
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Mapping, Optional

import yaml

SCHEMA = "lhre.study-inputs.v1"
RESOLVED_SCHEMA = "lhre.study-inputs.resolved.v1"
STATUSES = frozenset({"measured", "cad_export", "declared", "assumed", "guessed", "unavailable"})
KINDS = frozenset({"transcription", "cad_export"})
RELEASE_STATUSES = frozenset({"candidate", "released", "unavailable"})
# Same release grade as the vehicle bundle checker (contract C7).
RELEASE_GRADE = frozenset({"measured", "cad_export", "declared"})
STORED = frozenset({"git", "external"})
POINTS = ("lower_fore_i", "lower_aft_i", "lower_o", "upper_fore_i", "upper_aft_i", "upper_o", "tie_o", "wheel_center")
GEOMETRY_UNITS = {"mm": lambda v: v / 1000.0, "m": lambda v: v}
GEOMETRY_KEYS = frozenset({
    "label", "kind", "release_status", "release_source", "release", "transcription_of", "export_file", "units",
    "axes", "origin_xy", "datum_z", "mirror", "points", "rack_pickup",
})
CONVERT = {
    ("mm", "m"): lambda v: v / 1000.0,
    ("m", "mm"): lambda v: v * 1000.0,
    ("in", "m"): lambda v: v * 0.0254,
    ("lb", "kg"): lambda v: v * 0.45359237,
    ("deg", "rad"): math.radians,
    ("rad", "deg"): math.degrees,
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StudyInputsError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Field:
    key: str
    value: object
    status: str
    source: str
    unit: Optional[str] = None
    file_value: object = None
    file_unit: Optional[str] = None
    override: bool = False

    def record(self):
        return {k: v for k, v in dataclasses.asdict(self).items() if k != "key"}


@dataclasses.dataclass(frozen=True)
class FrontGeometry:
    label: str
    kind: str
    release_status: str
    release_source: str
    source_file: dict
    units: str
    frame: dict
    points_m: dict
    rack_pickup_m: list
    points_field: Field
    rack_field: Field

    def record(self):
        return {
            "label": self.label, "kind": self.kind, "release_status": self.release_status,
            "release_source": self.release_source, "source_file": self.source_file, "file_units": self.units,
            "frame": {k: v.record() for k, v in self.frame.items()},
            "points_m": self.points_m, "rack_pickup_m": self.rack_pickup_m,
            "points": self.points_field.record(), "rack_pickup": self.rack_field.record(),
        }


@dataclasses.dataclass(frozen=True)
class StudyInputs:
    path: Path
    sha256: str
    study: str
    bundle_id: Field
    geometry: FrontGeometry
    fields: dict

    def value(self, key):
        return self.fields[key].value

    def override(self, key, value, source):
        old = self.fields[key]
        if old.unit is not None and not (isinstance(value, (int, float)) and math.isfinite(value)):
            raise StudyInputsError(f"Override {key} must be a finite number. Got {value!r}.")
        new = dataclasses.replace(old, value=value, source=source, file_value=old.file_value, override=True)
        return dataclasses.replace(self, fields={**self.fields, key: new})


def file_record(path):
    path = Path(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _where(path, key):
    return f"{path.name}: {key}"


def _triple(path, key, node, unit=None):
    if not isinstance(node, dict):
        raise StudyInputsError(f"{_where(path, key)} must be a mapping with value, status and source.")
    missing = [k for k in ("value", "status", "source") if k not in node]
    if missing:
        raise StudyInputsError(f"{_where(path, key)} has no {', '.join(missing)}.")
    extra = set(node) - {"value", "status", "source", "unit"}
    if extra:
        raise StudyInputsError(f"{_where(path, key)} has unknown keys {sorted(extra)}.")
    status, source, value = node["status"], node["source"], node["value"]
    if status not in STATUSES:
        raise StudyInputsError(f"{_where(path, key)} has status {status!r}. Use one of {sorted(STATUSES)}.")
    if not isinstance(source, str) or not source.strip():
        raise StudyInputsError(f"{_where(path, key)} needs a non-empty source.")
    if (status == "unavailable") != (value is None):
        raise StudyInputsError(f"{_where(path, key)}: status unavailable goes with value null, and only then.")
    if unit is None:
        if "unit" in node:
            raise StudyInputsError(f"{_where(path, key)} is not a number and takes no unit.")
        return Field(key, value, status, source.strip())
    file_unit = node.get("unit")
    if file_unit is None:
        raise StudyInputsError(f"{_where(path, key)} needs a unit. The study reads it in {unit}.")
    if value is None:
        return Field(key, None, status, source.strip(), unit, None, file_unit)
    convert = (lambda v: v) if file_unit == unit else CONVERT.get((file_unit, unit))
    if convert is None:
        raise StudyInputsError(f"{_where(path, key)} is in {file_unit!r}. The study reads it in {unit!r} and cannot convert.")
    numbers = value if isinstance(value, list) else [value]
    if not numbers or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numbers):
        raise StudyInputsError(f"{_where(path, key)} must be a finite number or a list of them. Got {value!r}.")
    converted = [convert(float(v)) for v in numbers]
    return Field(key, converted if isinstance(value, list) else converted[0], status, source.strip(), unit, value, file_unit)


def _point(path, key, value):
    if not (isinstance(value, list) and len(value) == 3 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise StudyInputsError(f"{_where(path, key)} must be three finite numbers. Got {value!r}.")
    return [float(v) for v in value]


def _source_file(path, geometry):
    key = "transcription_of" if geometry["kind"] == "transcription" else "export_file"
    node = geometry.get(key)
    if not isinstance(node, dict):
        raise StudyInputsError(f"{_where(path, 'front_geometry')} of kind {geometry['kind']} needs {key}.")
    for field in ("name", "sha256", "stored"):
        if not node.get(field):
            raise StudyInputsError(f"{_where(path, f'front_geometry.{key}')} has no {field}.")
    if not SHA256.match(str(node["sha256"])):
        raise StudyInputsError(f"{_where(path, f'front_geometry.{key}.sha256')} must be 64 lowercase hex digits.")
    if node["stored"] not in STORED:
        raise StudyInputsError(f"{_where(path, f'front_geometry.{key}.stored')} must be one of {sorted(STORED)}.")
    record = {"field": key, **{k: node[k] for k in ("name", "sha256", "stored", "block") if k in node}}
    local = node.get("path")
    if local:
        local = Path(local) if Path(local).is_absolute() else path.parent / local
        if not local.is_file():
            raise StudyInputsError(f"{_where(path, f'front_geometry.{key}.path')}: {local} does not exist.")
        actual = hashlib.sha256(local.read_bytes()).hexdigest()
        if actual != node["sha256"]:
            raise StudyInputsError(f"{local} has SHA-256 {actual}. {path.name} declares {node['sha256']}.")
        record.update(path=str(local), verified=True)
    else:
        record["verified"] = False
        record["verified_note"] = "No local copy named. The run cannot check the declared hash."
    return record


def _check_release(path, kind, source_file, leaves):
    problems = [] if kind == "cad_export" else [f"kind is {kind}"]
    problems += [f"{key} is {field.status}" for key, field in leaves.items() if field.status not in RELEASE_GRADE]
    if not source_file.get("verified"):
        problems.append(f"the {source_file['field']} hash is not checked against a local copy")
    if problems:
        raise StudyInputsError(
            f"{_where(path, 'front_geometry')} is released, but {', '.join(problems)}. A released geometry needs kind "
            f"cad_export, a hash-checked export file and every leaf at {', '.join(sorted(RELEASE_GRADE))}.")


def _geometry(path, node):
    where = "front_geometry"
    if not isinstance(node, dict):
        raise StudyInputsError(f"{_where(path, where)} is missing.")
    extra = set(node) - GEOMETRY_KEYS
    if extra:
        raise StudyInputsError(f"{_where(path, where)} has unknown keys {sorted(extra)}.")
    for key in ("label", "kind", "release_status", "release_source", "units", "axes", "origin_xy", "datum_z", "mirror",
                "points", "rack_pickup"):
        if key not in node:
            raise StudyInputsError(f"{_where(path, f'{where}.{key}')} is missing.")
    if node["kind"] not in KINDS:
        raise StudyInputsError(f"{_where(path, f'{where}.kind')} must be one of {sorted(KINDS)}.")
    if node["release_status"] not in RELEASE_STATUSES:
        raise StudyInputsError(f"{_where(path, f'{where}.release_status')} must be one of {sorted(RELEASE_STATUSES)}.")
    if node["release_status"] == "released":
        release = node.get("release") or {}
        missing = [k for k in ("approved_by", "decision_ref", "date") if not release.get(k)]
        if missing:
            raise StudyInputsError(f"{_where(path, where)} is released but its release record has no {', '.join(missing)}.")
    if node["units"] not in GEOMETRY_UNITS:
        raise StudyInputsError(f"{_where(path, f'{where}.units')} is {node['units']!r}. Use one of {sorted(GEOMETRY_UNITS)}.")
    frame = {key: _triple(path, f"{where}.{key}", node[key]) for key in ("axes", "origin_xy", "datum_z", "mirror")}
    for key in ("axes", "origin_xy", "mirror"):
        if frame[key].value is None:
            raise StudyInputsError(f"{_where(path, f'{where}.{key}')} must be stated. The study cannot place the points without it.")
    points_field = _triple(path, f"{where}.points", node["points"])
    rack_field = _triple(path, f"{where}.rack_pickup", node["rack_pickup"])
    if node["kind"] == "transcription":
        for name, field in (("points", points_field), ("rack_pickup", rack_field)):
            if field.status in ("cad_export", "measured"):
                raise StudyInputsError(f"{_where(path, f'{where}.{name}')} is a transcription. "
                                       f"Its status cannot be {field.status}. Use declared, assumed or guessed.")
    points = points_field.value
    if not isinstance(points, dict) or set(points) != set(POINTS):
        got = sorted(points) if isinstance(points, dict) else points
        raise StudyInputsError(f"{_where(path, f'{where}.points')} must name exactly {list(POINTS)}. Got {got}.")
    to_m = GEOMETRY_UNITS[node["units"]]
    points_m = {f"{name}_m": [to_m(c) for c in _point(path, f"{where}.points.{name}", points[name])] for name in POINTS}
    rack_m = [to_m(c) for c in _point(path, f"{where}.rack_pickup", rack_field.value)]
    source_file = _source_file(path, node)
    if node["release_status"] == "released":
        _check_release(path, node["kind"], source_file, {**frame, "points": points_field, "rack_pickup": rack_field})
    return FrontGeometry(
        label=str(node["label"]), kind=node["kind"], release_status=node["release_status"],
        release_source=str(node["release_source"]), source_file=source_file, units=node["units"],
        frame=frame, points_m=points_m, rack_pickup_m=rack_m, points_field=points_field, rack_field=rack_field,
    )


def _flatten(node, prefix=""):
    out = {}
    for key, child in node.items():
        dotted = f"{prefix}{key}"
        if isinstance(child, dict) and "status" not in child:
            out.update(_flatten(child, f"{dotted}."))
        else:
            out[dotted] = child
    return out


def load_study_inputs(path, spec: Mapping[str, Optional[str]], study=None) -> StudyInputs:
    path = Path(path)
    if not path.is_file():
        raise StudyInputsError(f"Study inputs file {path} does not exist.")
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise StudyInputsError(f"{path.name} must be a YAML mapping.")
    if data.get("schema") != SCHEMA:
        raise StudyInputsError(f"{path.name} has schema {data.get('schema')!r}. Expected {SCHEMA!r}.")
    if study is not None and data.get("study") != study:
        raise StudyInputsError(f"{path.name} is for study {data.get('study')!r}, not {study!r}.")
    if "bundle_id" not in data:
        raise StudyInputsError(f"{_where(path, 'bundle_id')} is missing. Use status unavailable if no bundle exists.")
    bundle = _triple(path, "bundle_id", data["bundle_id"])
    geometry = _geometry(path, data.get("front_geometry"))
    rest = {k: v for k, v in data.items() if k not in ("schema", "study", "bundle_id", "front_geometry")}
    flat = _flatten(rest)
    missing = sorted(set(spec) - set(flat))
    if missing:
        raise StudyInputsError(f"{path.name} has no {', '.join(missing)}.")
    unknown = sorted(set(flat) - set(spec))
    if unknown:
        raise StudyInputsError(f"{path.name} has keys the study does not read: {', '.join(unknown)}.")
    fields = {key: _triple(path, key, flat[key], spec[key]) for key in spec}
    return StudyInputs(path, hashlib.sha256(raw).hexdigest(), str(data.get("study")), bundle, geometry, fields)


def resolve(inputs: StudyInputs, files: Mapping[str, object], extra: Optional[Mapping] = None):
    return {
        "schema": RESOLVED_SCHEMA,
        "study": inputs.study,
        "inputs_file": {"path": str(inputs.path), "sha256": inputs.sha256},
        "bundle_id": inputs.bundle_id.record(),
        "front_geometry": inputs.geometry.record(),
        "values": {key: field.record() for key, field in sorted(inputs.fields.items())},
        "overrides": sorted(key for key, field in inputs.fields.items() if field.override),
        "files": {name: file_record(p) if isinstance(p, (str, Path)) else p for name, p in files.items()},
        **(extra or {}),
    }


def write_resolved(out_dir, resolved):
    path = Path(out_dir) / "inputs.resolved.json"
    path.write_text(json.dumps(resolved, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return path


def _number(v):
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _shown(record):
    if record.get("override"):
        value, unit, note = record["value"], record.get("unit"), " (override)"
    else:
        value, unit, note = record.get("file_value"), record.get("file_unit"), ""
        if value is None:
            value, unit = record["value"], None
    if value is None:
        return "unavailable"
    text = ", ".join(_number(v) for v in value) if isinstance(value, list) else _number(value)
    return text + (f" {unit}" if unit and unit != "1" else "") + note


def _cell(text):
    return str(text).replace("|", "/").replace("\n", " ")


def markdown_table(resolved):
    geometry = resolved["front_geometry"]
    source = geometry["source_file"]
    rows = [
        ("Vehicle bundle", _shown(resolved["bundle_id"]), resolved["bundle_id"]["status"], resolved["bundle_id"]["source"]),
        ("Front geometry", f"{geometry['label']}, {geometry['kind']}, release status {geometry['release_status']}",
         geometry["points"]["status"],
         f"{source['name']} SHA-256 {source['sha256'][:16]}…, stored {source['stored']}, "
         f"{'hash checked' if source.get('verified') else 'hash not checked by the run'}. {geometry['points']['source']}"),
    ]
    for key in ("axes", "origin_xy", "datum_z", "mirror"):
        record = geometry["frame"][key]
        rows.append((f"Front geometry {key}", _shown(record), record["status"], record["source"]))
    for key, record in resolved["values"].items():
        rows.append((key, _shown(record), record["status"], record["source"]))
    lines = ["| Input | Value | Status | Source |", "| ----- | ----- | ------ | ------ |"]
    lines += [f"| {' | '.join(_cell(c) for c in row)} |" for row in rows]
    return "\n".join(lines) + "\n"
