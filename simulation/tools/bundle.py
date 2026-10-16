import argparse
import datetime
import hashlib
import importlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard

import yaml

from common.vehicle_io import load_yaml, tire_template_name, tire_templates_root
from simulations.envelope.vehicle_loader import load_active_envelope_inputs

REPO = Path(__file__).resolve().parents[2]
BUNDLE = "vehicle/bundle.yml"
COMMITTED_VEHICLE = "vehicle/vehicle.yml"
SCHEMA = "lhre.vehicle-bundle.v1"
STATUSES = ("draft", "candidate", "released", "superseded")
EVIDENCE = ("measured", "cad_export", "declared", "assumed", "guessed", "unavailable")
RELEASE_GRADE = ("measured", "cad_export", "declared")
SOURCE_KINDS = ("lotus-shark", "cad-export", "hand-entered", "copied-model")
STORED = ("in-bundle", "external")
LICENSES = ("public", "team-private", "restricted")
AXLES = ("front", "rear")
REACTIONS = ("chassis", "upright")
FRAME_AXES = {"x": "forward", "y": "left", "z": "up"}
FRAME_UNITS = {"length": "m", "mass": "kg", "alignment_angle": "deg"}
SHARE_SUM_TOLERANCE = 1e-9
NOT_APPROVAL = "software checks passed. Not hardware approval."
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256 = re.compile(r"[0-9a-f]{64}")
# The container runs git as root on files that the host or CI runner owns.
SAFE_DIRECTORY = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"}

Check = Callable[[Any], "str | None"]


@dataclass(frozen=True)
class Problem:
    path: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '(bundle)'}: {self.message} [{self.code}]"


class BundleError(ValueError):
    pass


def _anything(value: Any) -> str | None:
    return None


def _text(value: Any) -> str | None:
    return None if isinstance(value, str) and value.strip() else "must be a non-empty string"


def _slug(value: Any) -> str | None:
    if isinstance(value, str) and SLUG.fullmatch(value):
        return None
    return "must be a slug: lower-case letters and digits, joined by single hyphens"


def _sha256(value: Any) -> str | None:
    return None if isinstance(value, str) and SHA256.fullmatch(value) else "must be 64 lower-case hex digits"


def _relative_path(value: Any) -> str | None:
    if isinstance(value, str) and value and "\\" not in value:
        path = PurePosixPath(value)
        if not path.is_absolute() and ".." not in path.parts:
            return None
    return "must be a path from the repo root, with / and no .."


def _share(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
        return None
    return "must be a number from 0 to 1"


def _date(value: Any) -> str | None:
    if isinstance(value, datetime.date):
        return None
    try:
        datetime.date.fromisoformat(str(value))
    except ValueError:
        return "must be an ISO date, for example 2026-10-14"
    return None


def _axle_list(value: Any) -> str | None:
    if isinstance(value, list) and value and len(set(value)) == len(value) and set(value) <= set(AXLES):
        return None
    return "must list front, rear or both, each once"


def _one_of(*choices: Any) -> Check:
    def check(value: Any) -> str | None:
        return None if value in choices else f"must be one of: {', '.join(map(str, choices))}"

    return check


def _fixed(expected: Any) -> Check:
    def check(value: Any) -> str | None:
        return None if value == expected else f"must be {expected!r}, the only convention of boblib.vehicle.v1"

    return check


@dataclass(frozen=True)
class Plain:
    check: Check
    nullable: bool = False


@dataclass(frozen=True)
class Leaf:
    check: Check = _anything


@dataclass(frozen=True)
class Each:
    item: dict


_REACTION = Leaf(_one_of(*REACTIONS))
_SHARED_PATH = {"front_share": Leaf(_share), "rear_share": Leaf(_share), "reaction": _REACTION}
_AXLE = {
    "revision": Leaf(_text),
    "source": Leaf(_slug),
    "transform": Leaf(),
    "cad": Leaf(),
    "svn": Leaf(),
}
LAYOUT = {
    "schema": Plain(_fixed(SCHEMA)),
    "bundle_id": Plain(_slug),
    "status": Plain(_one_of(*STATUSES)),
    "superseded_by": Plain(_slug, nullable=True),
    "release": {"approved_by": Leaf(_text), "decision_ref": Leaf(_text), "date": Leaf(_date)},
    "members": {
        "vehicle": {"path": Plain(_relative_path), "sha256": Plain(_sha256), "schema": Plain(_text)},
        "tires": Each(
            {
                "id": Plain(_text),
                "path": Plain(_relative_path),
                "sha256": Plain(_sha256),
                "axles": Plain(_axle_list),
                "license": Leaf(_one_of(*LICENSES)),
                "source": Leaf(_text),
            }
        ),
        "sources": Each(
            {
                "id": Plain(_slug),
                "kind": Plain(_one_of(*SOURCE_KINDS)),
                "file": {"name": Plain(_text), "sha256": Plain(_sha256), "stored": Plain(_one_of(*STORED))},
                "units": Leaf(),
            }
        ),
    },
    "frame": {
        "axes": Leaf(_fixed(FRAME_AXES)),
        "units": Leaf(_fixed(FRAME_UNITS)),
        "datum_z": {"front": Leaf(), "rear": Leaf()},
        "mirror": {"stored_side": Leaf(_fixed("left")), "rule": Leaf(_fixed("negate_y"))},
    },
    "axles": {"front": _AXLE, "rear": _AXLE},
    "setup": {"id": Leaf(_text), "source": Leaf(_text)},
    "torque_topology": {
        "drive": _SHARED_PATH,
        "hydraulic_brake": {"front": {"reaction": _REACTION}, "rear": {"reaction": _REACTION}},
        "regen": _SHARED_PATH,
    },
}
LEAF_KEYS = frozenset({"value", "status", "source"})


def _join(prefix: str, key: object) -> str:
    return f"{prefix}.{key}" if prefix else str(key)


def _mapping(node: Any, *keys: str) -> dict:
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _items(node: Any, *keys: str) -> list[dict]:
    parent = _mapping(node, *keys[:-1])
    items = parent.get(keys[-1])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _is_leaf(node: Any) -> TypeGuard[dict]:
    return isinstance(node, dict) and set(node) == LEAF_KEYS


def _leaves(spec: Any, node: Any, path: str = "") -> Iterator[tuple[str, dict]]:
    if isinstance(spec, Leaf):
        if _is_leaf(node) and node["status"] in EVIDENCE:
            yield path, node
    elif isinstance(spec, Each) and isinstance(node, list):
        for index, item in enumerate(node):
            yield from _leaves(spec.item, item, f"{path}[{index}]")
    elif isinstance(spec, dict) and isinstance(node, dict):
        for key, child in spec.items():
            yield from _leaves(child, node.get(key), _join(path, key))


def _values(node: Any) -> Any:
    if _is_leaf(node):
        return node["value"]
    if isinstance(node, dict):
        return {key: _values(child) for key, child in node.items()}
    if isinstance(node, list):
        return [_values(child) for child in node]
    return node


def _digest(data: Any) -> str:
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tire_is_public(tire: Mapping[str, Any]) -> bool:
    license = tire.get("license")
    return _is_leaf(license) and license["value"] == "public" and license["status"] in RELEASE_GRADE


@dataclass(frozen=True)
class Bundle:
    path: Path
    data: dict = field(repr=False)

    @property
    def bundle_id(self) -> str | None:
        return self.data.get("bundle_id")

    @property
    def status(self) -> str | None:
        return self.data.get("status")

    def member_hashes(self) -> dict[str, Any]:
        hashes = {"vehicle": _mapping(self.data, "members", "vehicle").get("sha256")}
        for tire in _items(self.data, "members", "tires"):
            hashes[f"tire:{tire.get('id')}"] = tire.get("sha256")
        for source in _items(self.data, "members", "sources"):
            hashes[f"source:{source.get('id')}"] = _mapping(source, "file").get("sha256")
        return hashes

    def dependency_keys(self) -> dict[str, str]:
        keys = {name: digest for name, digest in self.member_hashes().items() if isinstance(digest, str)}
        blocks = {
            "frame": _mapping(self.data, "frame"),
            "axle:front": _mapping(self.data, "axles", "front"),
            "axle:rear": _mapping(self.data, "axles", "rear"),
            "setup": _mapping(self.data, "setup"),
            "torque_topology": _mapping(self.data, "torque_topology"),
        }
        keys.update({name: _digest(_values(block)) for name, block in blocks.items()})
        return keys

    def restricted_files(self) -> list[str]:
        files = [tire.get("path") for tire in _items(self.data, "members", "tires") if not tire_is_public(tire)]
        files += [
            _mapping(source, "file").get("name")
            for source in _items(self.data, "members", "sources")
            if _mapping(source, "file").get("stored") == "in-bundle"
        ]
        return [str(path) for path in files if path]


def load_bundle(path: str | os.PathLike[str]) -> Bundle:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BundleError(f"{path}: a bundle must be a YAML mapping")
    return Bundle(path, data)


class _Checker:
    def __init__(self) -> None:
        self.problems: list[Problem] = []

    def add(self, path: str, code: str, message: str) -> None:
        self.problems.append(Problem(path, code, message))

    def walk(self, spec: Any, node: Any, path: str) -> None:
        if isinstance(spec, dict):
            if not isinstance(node, dict):
                self.add(path, "wrong_type", "must be a mapping.")
                return
            for key, child in spec.items():
                if key in node:
                    self.walk(child, node[key], _join(path, key))
                else:
                    hint = " Write an unknown value as {value: null, status: unavailable, source: <why>}."
                    self.add(_join(path, key), "missing_key", "is missing." + (hint if isinstance(child, Leaf) else ""))
            for key in node:
                if key not in spec:
                    self.add(_join(path, key), "unknown_key", f"is not a {SCHEMA} key.")
        elif isinstance(spec, Each):
            if not isinstance(node, list):
                self.add(path, "wrong_type", "must be a list.")
                return
            for index, item in enumerate(node):
                self.walk(spec.item, item, f"{path}[{index}]")
        elif isinstance(spec, Leaf):
            self.leaf(spec, node, path)
        elif not (node is None and spec.nullable):
            error = spec.check(node)
            if error:
                self.add(path, "bad_value", f"is {node!r}. It {error}.")

    def leaf(self, spec: Leaf, node: Any, path: str) -> None:
        if not _is_leaf(node):
            self.add(
                path,
                "not_a_triple",
                f"is {node!r}. It must be {{value, status, source}}. "
                "Write an unknown value as {value: null, status: unavailable, source: <why>}.",
            )
            return
        value, status = node["value"], node["status"]
        if status not in EVIDENCE:
            self.add(path, "bad_status", f"status {status!r} is not one of: {', '.join(EVIDENCE)}.")
            return
        if _text(node["source"]):
            self.add(path, "missing_source", "source must say where the value comes from, or why it is unavailable.")
        if status == "unavailable":
            if value is not None:
                self.add(path, "unavailable_has_value", f"status is unavailable, so value must be null, not {value!r}.")
        elif value is None:
            self.add(path, "missing_value", f"value is null, so status must be unavailable, not {status}.")
        else:
            error = spec.check(value)
            if error:
                self.add(path, "bad_value", f"value {value!r} {error}.")


def vehicle_file(
    bundle: Bundle, vehicle_path: str | os.PathLike[str] | None = None, *, root: Path | None = None
) -> Path | None:
    if vehicle_path is not None:
        # BobSim resolves a relative vehicle path from the BobSim root, not from the working directory.
        return Path(vehicle_path).absolute()
    member = _mapping(bundle.data, "members", "vehicle").get("path")
    if not isinstance(member, str) or _relative_path(member):
        return None
    return (root or REPO) / member


def _show(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return str(path)


def _check_hash(c: _Checker, path: str, file: Path, expected: Any) -> bool:
    if _sha256(expected):
        return False
    if not file.is_file():
        c.add(path, "missing_file", f"{_show(file)} does not exist.")
        return False
    data = file.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        crlf = " The file has CRLF line ends. Hashes use the LF bytes that git commits." if b"\r\n" in data else ""
        c.add(
            path,
            "sha256_mismatch",
            f"{_show(file)} has sha256 {actual}. The bundle records {expected}. "
            f"A changed member needs its new sha256 and a new bundle_id.{crlf}",
        )
        return False
    return True


def _check_unique(c: _Checker, items: list[dict], path: str) -> None:
    seen: set[Any] = set()
    for item in items:
        if item.get("id") in seen:
            c.add(path, "duplicate_id", f"id {item.get('id')!r} appears more than once.")
        seen.add(item.get("id"))


def _check_tires(c: _Checker, tires: list[dict], vehicle: dict) -> None:
    index = {tire.get("id"): number for number, tire in enumerate(tires)}
    uses: dict[str, str] = {}
    for axle in AXLES:
        try:
            uses[axle] = tire_template_name(vehicle, vehicle[axle])
        except (KeyError, TypeError, AttributeError) as error:
            c.add("members.tires", "tire_mismatch", f"vehicle.yml {axle} names no tire: {error}.")
    names = set(uses.values())
    aero = _mapping(vehicle, "aero").get("tire_template")
    if aero:
        names.add(str(aero))
    folder = tire_templates_root(vehicle)
    for name in sorted(names):
        if name not in index:
            c.add("members.tires", "tire_mismatch", f"vehicle.yml loads tire {name!r}. No bundle tire has that id.")
            continue
        loaded, expected = folder / f"{name}.tir", tires[index[name]].get("sha256")
        if not loaded.is_file():
            c.add("members.tires", "tire_mismatch", f"vehicle.yml loads {loaded}, which does not exist.")
        elif isinstance(expected, str) and file_sha256(loaded) != expected:
            c.add(
                f"members.tires[{index[name]}].sha256",
                "tire_mismatch",
                f"vehicle.yml loads {loaded} with sha256 {file_sha256(loaded)}. The bundle tire records {expected}.",
            )
    for axle, name in uses.items():
        listed = tires[index[name]].get("axles") if name in index else None
        if isinstance(listed, list) and axle not in listed:
            c.add("members.tires", "tire_mismatch", f"vehicle.yml uses {name!r} on the {axle} axle. Its axles omit {axle}.")
    for number, tire in enumerate(tires):
        listed = tire.get("axles")
        for axle in listed if isinstance(listed, list) else []:
            if axle in uses and uses[axle] != tire.get("id"):
                c.add(
                    f"members.tires[{number}].axles",
                    "tire_mismatch",
                    f"lists the {axle} axle, but vehicle.yml uses {uses[axle]!r} there.",
                )


def _check_members(c: _Checker, data: dict, root: Path, vehicle: Path | None) -> None:
    member = _mapping(data, "members", "vehicle")
    tires = _items(data, "members", "tires")
    sources = _items(data, "members", "sources")
    vehicle_ok = vehicle is not None and _check_hash(c, "members.vehicle.sha256", vehicle, member.get("sha256"))
    for number, tire in enumerate(tires):
        if _relative_path(tire.get("path")) is None:
            _check_hash(c, f"members.tires[{number}].sha256", root / tire["path"], tire.get("sha256"))
    for number, source in enumerate(sources):
        file = _mapping(source, "file")
        if file.get("stored") == "in-bundle" and _relative_path(file.get("name")) is None:
            _check_hash(c, f"members.sources[{number}].file.sha256", root / file["name"], file.get("sha256"))
    _check_unique(c, tires, "members.tires")
    _check_unique(c, sources, "members.sources")
    if not vehicle_ok:
        return
    try:
        parsed = load_yaml(vehicle)
    except (OSError, TypeError, yaml.YAMLError) as error:
        c.add("members.vehicle.path", "vehicle_unreadable", f"{vehicle} is not a vehicle YAML mapping: {error}")
        return
    if isinstance(member.get("schema"), str) and parsed.get("schema") != member["schema"]:
        c.add(
            "members.vehicle.schema",
            "schema_mismatch",
            f"is {member['schema']!r}, but {vehicle} has schema {parsed.get('schema')!r}.",
        )
    _check_tires(c, tires, parsed)


def _check_sources(c: _Checker, data: dict) -> None:
    sources = {source.get("id"): source for source in _items(data, "members", "sources")}
    for axle in AXLES:
        leaf = _mapping(data, "axles", axle).get("source")
        if not _is_leaf(leaf) or leaf["value"] is None:
            continue
        source = sources.get(leaf["value"])
        path = f"axles.{axle}.source"
        if source is None:
            c.add(path, "unknown_source", f"value {leaf['value']!r} is not an id in members.sources.")
        elif source.get("kind") == "hand-entered" and leaf["status"] != "guessed":
            c.add(
                path,
                "hand_entered",
                f"source {leaf['value']!r} is hand-entered, so the status must be guessed, not {leaf['status']}. "
                "A hand transcription is not a coordinate export.",
            )


def _check_topology(c: _Checker, data: dict) -> None:
    for name in ("drive", "regen"):
        block = _mapping(data, "torque_topology", name)
        shares = [block.get(key) for key in ("front_share", "rear_share")]
        values = [leaf["value"] for leaf in shares if _is_leaf(leaf) and _share(leaf["value"]) is None]
        if len(values) == 2 and abs(sum(values) - 1) > SHARE_SUM_TOLERANCE:
            c.add(
                f"torque_topology.{name}",
                "share_sum",
                f"front_share {values[0]} plus rear_share {values[1]} is {sum(values)}, not 1.",
            )


def _check_status(c: _Checker, data: dict, root: Path, vehicle: Path | None) -> None:
    status, replacement = data.get("status"), data.get("superseded_by")
    if status == "superseded" and replacement is None:
        c.add("superseded_by", "missing_value", "A superseded bundle names the bundle_id that replaces it.")
    elif status in STATUSES and status != "superseded" and replacement is not None:
        c.add("superseded_by", "bad_value", f"must be null while status is {status}.")
    if status != "released":
        return
    for path, leaf in _leaves(LAYOUT, data):
        if leaf["status"] not in RELEASE_GRADE:
            c.add(
                path,
                "release_blocked",
                f"status is {leaf['status']}. A released bundle needs {', '.join(RELEASE_GRADE)} here.",
            )
    committed = (root / COMMITTED_VEHICLE).resolve()
    member = _mapping(data, "members", "vehicle").get("path")
    if member != COMMITTED_VEHICLE or vehicle is None or vehicle.resolve() != committed:
        c.add(
            "status",
            "release_not_committed_vehicle",
            f"Only {COMMITTED_VEHICLE} can be released. Check a copy as a candidate.",
        )


def check_bundle(
    bundle: Bundle, vehicle_path: str | os.PathLike[str] | None = None, *, root: Path | None = None
) -> list[Problem]:
    root = root or REPO
    c = _Checker()
    c.walk(LAYOUT, bundle.data, "")
    vehicle = vehicle_file(bundle, vehicle_path, root=root)
    _check_members(c, bundle.data, root, vehicle)
    _check_sources(c, bundle.data)
    _check_topology(c, bundle.data)
    _check_status(c, bundle.data, root, vehicle)
    return c.problems


def _without_status(data: dict) -> dict:
    return {key: value for key, value in data.items() if key not in ("status", "superseded_by")}


def check_immutable(base: Bundle | None, head: Bundle) -> list[Problem]:
    if base is None or base.bundle_id != head.bundle_id:
        return []
    problems = []
    old, new = base.member_hashes(), head.member_hashes()
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    if changed:
        problems.append(
            Problem(
                "bundle_id",
                "immutable_members",
                f"{head.bundle_id!r} is also the bundle_id in {base.path}, but these member hashes changed: "
                f"{', '.join(changed)}. A changed member needs a new bundle_id.",
            )
        )
    if base.status == "released" and (
        head.status not in ("released", "superseded") or _without_status(base.data) != _without_status(head.data)
    ):
        problems.append(
            Problem(
                "bundle_id",
                "immutable_released",
                f"{head.bundle_id!r} is released in {base.path}. It can only change to superseded. "
                "Any other change needs a new bundle_id.",
            )
        )
    return problems


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **SAFE_DIRECTORY},
    )


def load_base_bundle(ref: str, *, repo: Path | None = None) -> Bundle | None:
    repo = REPO if repo is None else repo
    resolved = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if resolved.returncode != 0:
        raise BundleError(
            f"git cannot resolve the base ref {ref!r} in {repo}. {resolved.stderr.strip()} "
            "Fetch the ref first. In a git worktree, the container cannot read the .git link, so run the check in a clone."
        )
    commit = resolved.stdout.strip()
    if _git(repo, "cat-file", "-e", f"{commit}:{BUNDLE}").returncode != 0:
        return None
    shown = _git(repo, "show", f"{commit}:{BUNDLE}")
    if shown.returncode != 0:
        raise BundleError(f"git cannot read {BUNDLE} at {ref}: {shown.stderr.strip()}")
    data = yaml.safe_load(shown.stdout)
    if not isinstance(data, dict):
        raise BundleError(f"{BUNDLE} at {ref} is not a YAML mapping")
    return Bundle(Path(f"{ref}:{BUNDLE}"), data)


def bobsim_checks(vehicle: Path) -> tuple[list[Problem], list[str]]:
    problems, lines = [], []
    try:
        inputs = load_active_envelope_inputs(vehicle)
        lines.append(f"BobSim load: OK ({type(inputs).__name__})")
    except Exception as error:  # BobSim raises many error types for a bad vehicle file.
        problems.append(Problem("members.vehicle.path", "bobsim_load", f"BobSim cannot load {vehicle}: {error}"))
    if importlib.util.find_spec("common.vehicle_schema") is None:
        lines.append(
            "BobSim vehicle validation: not available at this BobSim pin (no common.vehicle_schema). "
            "Only the bundle rules and the BobSim load ran."
        )
        return problems, lines
    schema = importlib.import_module("common.vehicle_schema")
    found = schema.validate_vehicle_file(vehicle, strict=False)
    problems += [Problem(f"vehicle:{item.path}", f"bobsim_{item.code}", item.message) for item in found]
    lines.append(f"BobSim vehicle validation: {f'{len(found)} problem(s)' if found else 'passed'}")
    return problems, lines


def _verdict(bundle: Bundle) -> str:
    status = bundle.status
    if status == "released":
        release = _values(_mapping(bundle.data, "release"))
        return (
            f"released: software checks passed. The release record names {release.get('approved_by')}, "
            f"{release.get('decision_ref')} and {release.get('date')}. This tool checks that the record exists, "
            "not that it is correct."
        )
    if status == "superseded":
        return (
            f"superseded by {bundle.data.get('superseded_by')}: {NOT_APPROVAL} "
            f"Results that cite {bundle.bundle_id} are stale."
        )
    return f"{status}: {NOT_APPROVAL}"


def _check_command(bundle: Bundle, vehicle_path: str | None, base_ref: str | None) -> int:
    problems = check_bundle(bundle, vehicle_path)
    vehicle = vehicle_file(bundle, vehicle_path)
    shown = _show(vehicle) if vehicle is not None else None
    lines = [f"{_show(bundle.path)}: bundle {bundle.bundle_id} ({bundle.status}), vehicle {shown}"]
    if vehicle is not None and vehicle.is_file():
        found, info = bobsim_checks(vehicle)
        problems += found
        lines += info
    committed = REPO / BUNDLE
    if committed.is_file() and bundle.path.resolve() != committed.resolve():
        try:
            problems += check_immutable(load_bundle(committed), bundle)
        except (yaml.YAMLError, BundleError) as error:
            problems.append(Problem("bundle_id", "committed_unreadable", f"Cannot compare with {BUNDLE}: {error}"))
    if base_ref is None:
        lines.append("Immutability: not checked against a base. Pass --base <git ref> to check it.")
    else:
        try:
            base = load_base_bundle(base_ref)
        except BundleError as error:
            print(f"FAIL: {error}")
            return 2
        if base is None:
            lines.append(f"Immutability: {base_ref} has no {BUNDLE}. Nothing to compare.")
        else:
            problems += check_immutable(base, bundle)
            lines.append(f"Immutability: compared with {base.bundle_id} at {base_ref}.")
    print("\n".join(lines))
    if problems:
        print("\n".join(str(problem) for problem in problems))
        print(f"FAIL: {len(problems)} problem(s). The bundle is not accepted as {bundle.status}.")
        return 1
    gaps = [
        f"  {path}: {leaf['status']}" for path, leaf in _leaves(LAYOUT, bundle.data) if leaf["status"] not in RELEASE_GRADE
    ]
    if gaps:
        print(f"Not release grade ({len(gaps)}):")
        print("\n".join(gaps))
    print(_verdict(bundle))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bundle.py", description=f"Check {BUNDLE}, the {SCHEMA} metadata sidecar.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check the bundle rules, the member hashes and the BobSim load.")
    check.add_argument("--bundle", help=f"Bundle file. Default: {BUNDLE} in the repo.")
    check.add_argument("--vehicle", help="Vehicle file to check in place of members.vehicle.path, such as a copy under out/.")
    check.add_argument("--base", help="Git ref. Fail when a member hash changed and the bundle_id did not.")
    restricted = commands.add_parser("restricted", help="List member files that must not leave the repo.")
    restricted.add_argument("--bundle", help=f"Bundle file. Default: {BUNDLE} in the repo.")
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle(args.bundle or REPO / BUNDLE)
    except (OSError, yaml.YAMLError, BundleError) as error:
        print(f"FAIL: {error}")
        return 2
    if args.command == "restricted":
        print("\n".join(bundle.restricted_files()))
        return 0
    return _check_command(bundle, args.vehicle, args.base)


if __name__ == "__main__":
    sys.exit(main())
