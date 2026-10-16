"""Dependency keys of a study result and the stale check.

python tools/invalidation.py stale out/<name>/provenance.json [--vehicle PATH]
python tools/invalidation.py keys <name> [--vehicle PATH]
"""

import argparse
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

SCHEMA = "lhre.provenance.v2"
EXIT_CURRENT, EXIT_STALE, EXIT_UNKNOWN = 0, 1, 3
ABSENT = "absent"
METHOD = (
    "Content digests of the inputs. Python files are hashed by their tokens and YAML files by their parsed "
    "values, so comments and layout do not count. Docs and figures do not count. A null key means the input "
    "could not be read."
)

BOBSIM_PHYSICS = ("common", "engines", "simulations", "optimization", "Dockerfile", "requirements.txt")
BOBLIB_PACKAGE = "BobLib"
BUNDLE_PHYSICS = ("frame", "axles", "setup", "torque_topology")
DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".pdf"})
SKIP_PARTS = frozenset({"__pycache__", ".pytest_cache", ".ipynb_checkpoints"})
TRIPLE = frozenset({"value", "status", "source"})
TRIPLE_METADATA = frozenset({"status", "source"})
YAML_SUFFIXES = frozenset({".yml", ".yaml"})
NOT_RECORDED, CANNOT_READ = "not recorded", "cannot read now"
# The container runs git as root on files that the host owns.
SAFE_DIRECTORY = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"}

_SKIPPED_TOKENS = frozenset({tokenize.COMMENT, tokenize.NL, tokenize.ENCODING})
_LAYOUT_TOKENS = frozenset({tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER})


class GitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Layout:
    repo: Path

    @classmethod
    def default(cls) -> "Layout":
        return cls(Path(__file__).resolve().parents[2])

    @property
    def sim(self) -> Path:
        return self.repo / "simulation"

    @property
    def bobsim(self) -> Path:
        return self.sim / "bobsim"

    @property
    def boblib(self) -> Path | None:
        gitmodules = self.bobsim / ".gitmodules"
        if not gitmodules.is_file():
            return None
        paths = re.findall(r"^\s*path\s*=\s*(\S+)\s*$", gitmodules.read_text(encoding="utf-8"), re.M)
        found = [path for path in paths if path.lower().endswith("boblib")]
        return self.bobsim / found[0] if len(found) == 1 else None

    def import_roots(self) -> tuple[Path, ...]:
        """The PYTHONPATH order of docker-compose.yml."""
        return (self.bobsim, self.sim)


@dataclass
class Keys:
    values: dict[str, str | None] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)

    def put(self, name: str, compute) -> None:
        try:
            self.values[name] = compute()
        except (OSError, GitUnavailable, ValueError, yaml.YAMLError) as error:
            self.values[name] = None
            self.unavailable[name] = str(error)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def python_digest(source: bytes) -> str:
    digest = hashlib.sha256()
    try:
        for token in tokenize.tokenize(io.BytesIO(source.replace(b"\r\n", b"\n")).readline):
            if token.type in _SKIPPED_TOKENS:
                continue
            text = "" if token.type in _LAYOUT_TOKENS else token.string
            digest.update(f"{token.type}:{len(text)}:{text}".encode("utf-8", "surrogateescape"))
    except (tokenize.TokenError, SyntaxError):
        return hashlib.sha256(source).hexdigest()
    return digest.hexdigest()


def _without_metadata(node):
    if isinstance(node, Mapping):
        keep = node.keys() - TRIPLE_METADATA if TRIPLE <= node.keys() else node.keys()
        return {str(key): _without_metadata(node[key]) for key in keep}
    if isinstance(node, list):
        return [_without_metadata(value) for value in node]
    return node


def yaml_digest(source: bytes) -> str:
    try:
        documents = list(yaml.safe_load_all(source))
    except yaml.YAMLError:
        return hashlib.sha256(source).hexdigest()
    return _sha256_text(json.dumps(_without_metadata(documents), sort_keys=True, separators=(",", ":"), default=str))


def file_digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".py":
        return python_digest(data)
    return yaml_digest(data) if path.suffix in YAML_SUFFIXES else hashlib.sha256(data).hexdigest()


def is_doc(relative: PurePosixPath) -> bool:
    return (
        relative.suffix.lower() in DOC_SUFFIXES
        or relative.suffix == ".pyc"
        or bool(SKIP_PARTS.intersection(relative.parts))
    )


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            capture_output=True,
            env={**os.environ, **SAFE_DIRECTORY},
        )
    except OSError as error:
        raise GitUnavailable(f"git cannot run: {error}") from None
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitUnavailable(f"git cannot read {repo}: {message}")
    return result.stdout.decode("utf-8", "surrogateescape")


def git_root(repo: Path) -> Path:
    """Stop git from reading a parent repository when `repo` has no git data of its own."""
    top = Path(git(repo, "rev-parse", "--show-toplevel").strip())
    if top.resolve() != repo.resolve():
        raise GitUnavailable(f"git cannot read {repo}: it has no git data of its own (git found {top})")
    return repo


def tree_digests(repo: Path, entries: Iterable[str]) -> dict[str, str]:
    """Digest each entry from the working tree files that git tracks or does not ignore."""
    entries = tuple(entries)
    listing = git(git_root(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *entries)
    lines: dict[str, list[str]] = {entry: [] for entry in entries}
    for name in sorted(set(filter(None, listing.split("\0")))):
        relative = PurePosixPath(name)
        path = repo / name
        if is_doc(relative) or not path.is_file():
            continue
        entry = next((e for e in entries if name == e or name.startswith(f"{e}/")), None)
        if entry is not None:
            lines[entry].append(f"{name}\0{file_digest(path)}\n")
    return {entry: _sha256_text("".join(found)) for entry, found in lines.items()}


def _module_name(path: Path, roots: tuple[Path, ...]) -> tuple[str, bool] | None:
    for root in roots:
        if path.is_relative_to(root):
            parts = path.relative_to(root).with_suffix("").parts
            is_package = parts[-1] == "__init__"
            return ".".join(parts[:-1] if is_package else parts), is_package
    return None


def _module_files(dotted: str, roots: tuple[Path, ...]) -> list[Path]:
    parts = dotted.split(".")
    for root in roots:
        module = root.joinpath(*parts)
        for candidate in (module.with_suffix(".py"), module / "__init__.py"):
            if candidate.is_file():
                parents = [root.joinpath(*parts[:i], "__init__.py") for i in range(1, len(parts))]
                return [candidate, *(p for p in parents if p.is_file())]
    return []


def _imports(tree: ast.AST, module: tuple[str, bool] | None) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if module is None:
                    continue
                name, is_package = module
                package = name.split(".") if is_package else name.split(".")[:-1]
                package = package[: len(package) - node.level + 1]
                base = ".".join([*package, *([base] if base else [])])
            if base:
                yield base
            yield from (f"{base}.{alias.name}" if base else alias.name for alias in node.names)


def import_closure(start: Iterable[Path], layout: Layout) -> list[Path]:
    """Every lhre and BobSim source file that the start files import, directly or not."""
    roots = layout.import_roots()
    seen: set[Path] = set()
    queue = [Path(path) for path in start]
    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_bytes())
        except SyntaxError:
            continue
        for dotted in _imports(tree, _module_name(path, roots)):
            queue.extend(_module_files(dotted, roots))
    return sorted(seen)


def study_dir(layout: Layout, study: str) -> Path:
    return layout.sim / "studies" / study


@dataclass(frozen=True)
class StudySources:
    lhre: list[Path]
    bobsim: list[Path]


def study_sources(layout: Layout, study: str) -> StudySources:
    """The study's own files, and the lhre and BobSim code that it imports. Docs and figures are left out."""
    folder = study_dir(layout, study)
    own = [p for p in folder.rglob("*") if p.is_file() and not is_doc(PurePosixPath(p.relative_to(folder).as_posix()))]
    imported = import_closure([p for p in own if p.suffix == ".py"], layout)
    lhre = {p for p in imported if p.is_relative_to(layout.sim) and not p.is_relative_to(layout.bobsim)}
    return StudySources(sorted(lhre | set(own)), [p for p in imported if p.is_relative_to(layout.bobsim)])


def tire_files(vehicle: Mapping, layout: Layout) -> dict[str, Path]:
    """The .tir files BobSim reads for this vehicle: axle templates and the aero fallback."""
    names = []
    for side in ("front", "rear"):
        tire = (vehicle.get(side) or {}).get("tire") or {}
        if tire.get("template"):
            names.append(str(tire["template"]))
    aero = vehicle.get("aero") or {}
    if isinstance(aero, Mapping) and aero.get("tire_template"):
        names.append(str(aero["tire_template"]))
    paths = vehicle.get("paths")
    raw = paths.get("tire_templates") if isinstance(paths, Mapping) else None
    root = Path(str(raw or "common/tire_templates"))
    root = root if root.is_absolute() else layout.bobsim / root
    return {name: Path(os.path.normpath(root / f"{name}.tir")) for name in names}


def bundle_path(vehicle_path: Path) -> Path:
    return vehicle_path.parent / "bundle.yml"


def plain(node):
    """Read a metadata triple {value, status, source} as its value."""
    return node["value"] if isinstance(node, Mapping) and TRIPLE <= node.keys() else node


def _values_only(node):
    node = plain(node)
    if isinstance(node, Mapping):
        return {str(key): _values_only(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_values_only(value) for value in node]
    return node


def read_bundle(vehicle_path: Path) -> dict | None:
    path = bundle_path(vehicle_path)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} is not a YAML mapping")
    return dict(data)


def describes_vehicle(bundle: Mapping, vehicle_sha256: str) -> bool | None:
    member = ((bundle.get("members") or {}).get("vehicle") or {}).get("sha256")
    return None if member is None else str(member) == vehicle_sha256


def _bundle_id(vehicle_path: Path) -> str:
    bundle = read_bundle(vehicle_path)
    if bundle is None:
        return ABSENT
    value = plain(bundle.get("bundle_id"))
    if not value:
        raise ValueError(f"{bundle_path(vehicle_path)} has no bundle_id")
    return str(value)


def _bundle_physics(vehicle_path: Path) -> str:
    bundle = read_bundle(vehicle_path)
    if bundle is None:
        return ABSENT
    sections = {name: _values_only(bundle.get(name)) for name in BUNDLE_PHYSICS}
    text = json.dumps(sections, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _put_tires(keys: Keys, vehicle_path: Path, layout: Layout) -> None:
    try:
        vehicle = yaml.safe_load(vehicle_path.read_text(encoding="utf-8"))
        if not isinstance(vehicle, Mapping):
            raise ValueError(f"{vehicle_path} is not a YAML mapping")
    except (OSError, ValueError, yaml.YAMLError) as error:
        keys.values["tires"] = None
        keys.unavailable["tires"] = f"cannot read the tire names: {error}"
        return
    for name, path in tire_files(vehicle, layout).items():
        keys.put(f"tire:{name}", lambda p=path: sha256_file(p))


def _put_bobsim(keys: Keys, imported: list[Path], layout: Layout) -> None:
    try:
        digests = tree_digests(layout.bobsim, BOBSIM_PHYSICS)
    except GitUnavailable as error:
        digests = {}
        for entry in BOBSIM_PHYSICS:
            keys.values[f"bobsim:{entry}"] = None
            keys.unavailable[f"bobsim:{entry}"] = str(error)
    for entry, digest in digests.items():
        keys.values[f"bobsim:{entry}"] = digest
    covered = [layout.bobsim / entry for entry in BOBSIM_PHYSICS]
    for path in imported:
        if not any(path == c or path.is_relative_to(c) for c in covered):
            keys.put(f"bobsim:{path.relative_to(layout.bobsim).as_posix()}", lambda p=path: file_digest(p))


def _put_boblib(keys: Keys, layout: Layout) -> None:
    def compute():
        if layout.boblib is None:
            raise ValueError(f"{layout.bobsim / '.gitmodules'} names no BobLib submodule")
        return tree_digests(layout.boblib, [BOBLIB_PACKAGE])[BOBLIB_PACKAGE]

    keys.put(f"boblib:{BOBLIB_PACKAGE}", compute)


def dependency_keys(study: str, vehicle_path: Path, layout: Layout) -> Keys:
    keys = Keys()
    keys.put("vehicle", lambda: yaml_digest(vehicle_path.read_bytes()))
    _put_tires(keys, vehicle_path, layout)
    keys.put("bundle_id", lambda: _bundle_id(vehicle_path))
    keys.put("bundle_physics", lambda: _bundle_physics(vehicle_path))
    sources = study_sources(layout, study)
    for path in sources.lhre:
        keys.put(f"lhre:{path.relative_to(layout.repo).as_posix()}", lambda p=path: file_digest(p))
    _put_bobsim(keys, sources.bobsim, layout)
    _put_boblib(keys, layout)
    return Keys(dict(sorted(keys.values.items())), dict(sorted(keys.unavailable.items())))


@dataclass
class Verdict:
    status: str
    changed: list[tuple[str, str]] = field(default_factory=list)
    unknown: list[tuple[str, str]] = field(default_factory=list)


def _short(value: str) -> str:
    return value if len(value) <= 24 else value[:12]


def compare(recorded: Mapping[str, str | None], recorded_unavailable: Mapping[str, str], current: Keys) -> Verdict:
    verdict = Verdict("current")
    for key in sorted(recorded.keys() | current.values.keys()):
        if key not in current.values or key not in recorded:
            verdict.changed.append((key, "removed" if key not in current.values else "added"))
            continue
        old, new = recorded[key], current.values[key]
        if old is None:
            verdict.unknown.append((key, f"{NOT_RECORDED}: {recorded_unavailable.get(key, 'no reason given')}"))
        elif new is None:
            verdict.unknown.append((key, f"{CANNOT_READ}: {current.unavailable.get(key, 'no reason given')}"))
        elif old != new:
            verdict.changed.append((key, f"changed {_short(old)} -> {_short(new)}"))
    return _settle(verdict)


def _settle(verdict: Verdict) -> Verdict:
    verdict.status = "stale" if verdict.changed else "unknown" if verdict.unknown else "current"
    return verdict


def _check_bundle(verdict: Verdict, vehicle_path: Path) -> Verdict:
    try:
        bundle = read_bundle(vehicle_path)
        vehicle_sha256 = sha256_file(vehicle_path) if bundle is not None else ""
    except (OSError, ValueError, yaml.YAMLError) as error:
        verdict.unknown.append(("bundle", f"{CANNOT_READ}: {error}"))
        return _settle(verdict)
    if bundle is None:
        return verdict
    name, successor = plain(bundle.get("bundle_id")), plain(bundle.get("superseded_by"))
    if plain(bundle.get("status")) == "superseded":
        verdict.changed.append(("bundle_status", f"bundle {name} is superseded by {successor}"))
    describes = describes_vehicle(bundle, vehicle_sha256)
    if describes is False:
        verdict.changed.append(("bundle_vehicle", f"bundle {name} names another vehicle file sha256"))
    elif describes is None:
        verdict.unknown.append(("bundle_vehicle", f"bundle {name} has no members.vehicle.sha256"))
    return _settle(verdict)


def check(record: Mapping, vehicle_path: Path, layout: Layout) -> Verdict:
    return _check_bundle(_check_keys(record, vehicle_path, layout), vehicle_path)


def _check_keys(record: Mapping, vehicle_path: Path, layout: Layout) -> Verdict:
    study = str(record.get("study", ""))
    if record.get("schema") != SCHEMA:
        schema = record.get("schema", "v1")
        verdict = Verdict("unknown", unknown=[("*", f"provenance has no invalidation keys (schema {schema})")])
        old = record.get("vehicle_sha256")
        current = Keys()
        current.put("vehicle", lambda: sha256_file(vehicle_path))
        new = current.values["vehicle"]
        if old and new and old != new:
            verdict.status = "stale"
            verdict.changed.append(("vehicle", f"changed {_short(old)} -> {_short(new)}"))
        return verdict
    if not study or not study_dir(layout, study).is_dir():
        return Verdict("unknown", unknown=[("*", f"no study folder for study {study!r}")])
    invalidation = record.get("invalidation") or {}
    current = dependency_keys(study, vehicle_path, layout)
    return compare(invalidation.get("keys") or {}, invalidation.get("unavailable") or {}, current)


def _vehicle(explicit: str | None, layout: Layout) -> Path:
    return Path(explicit or os.environ.get("BOBSIM_VEHICLE") or layout.repo / "vehicle" / "vehicle.yml")


def _stale(args, layout: Layout) -> int:
    path = Path(args.provenance)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Cannot read {path}: {error}", file=sys.stderr)
        return 2
    verdict = check(record, _vehicle(args.vehicle, layout), layout)
    print(f"{verdict.status.upper()} {record.get('study', '?')}")
    for key, detail in verdict.changed:
        print(f"  {key}  {detail}")
    reasons: dict[str, list[str]] = {}
    for key, detail in verdict.unknown:
        reasons.setdefault(detail, []).append(key)
    for detail, names in reasons.items():
        print(f"  {', '.join(names)}  unknown, {detail}")
    if verdict.status == "stale":
        print("An input changed. Run the study again before you use this result.")
        return EXIT_STALE
    if verdict.status == "unknown":
        print("Some inputs cannot be compared. Do not treat this result as current.")
        return EXIT_UNKNOWN
    return EXIT_CURRENT


def _keys(args, layout: Layout) -> int:
    keys = dependency_keys(args.study, _vehicle(args.vehicle, layout), layout)
    print(json.dumps({"keys": keys.values, "unavailable": keys.unavailable}, indent=2))
    return EXIT_CURRENT


def main(argv: list[str] | None = None, layout: Layout | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare a study result with the current inputs.")
    commands = parser.add_subparsers(dest="command", required=True)
    stale = commands.add_parser("stale", help="Print CURRENT, STALE or UNKNOWN for a provenance.json.")
    stale.add_argument("provenance")
    stale.add_argument("--vehicle", help="Vehicle file. Default: BOBSIM_VEHICLE, then vehicle/vehicle.yml.")
    keys = commands.add_parser("keys", help="Print the current dependency keys of a study.")
    keys.add_argument("study")
    keys.add_argument("--vehicle", help="Vehicle file. Default: BOBSIM_VEHICLE, then vehicle/vehicle.yml.")
    args = parser.parse_args(argv)
    layout = layout or Layout.default()
    return _stale(args, layout) if args.command == "stale" else _keys(args, layout)


if __name__ == "__main__":
    sys.exit(main())
