"""Scenario files: boards/VCU/Sil/scenarios/<name>.yml, checked and compiled
into a scenario.Timeline.

The format, lhre.vcu-sil-scenario.v1, is in boards/VCU/Sil/README.md
(section Scenarios). Every problem in a file is reported at once, with its
line where it has one, before anything builds; the YAML reader's own come
alone, since the checks need the file read. check.py adds the warnings. The
loader reads a strict YAML: true, false, null and decimal numbers as YAML
1.2 reads them, everything else as text, and a leading zero (010), which
YAML 1.1 and 1.2 read differently, not at all. It needs PyYAML and nothing
else outside the standard library, so the check runs anywhere Python 3.11 or
newer does.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .cosim import STEP_MS
from .firmware import EVENT_MODES, HVC_STATES
from .scenario import DEFAULTS, Segment, Timeline

SCHEMA = 'lhre.vcu-sil-scenario.v1'
_ANY_VERSION = re.compile(r'lhre\.vcu-sil-scenario\.v\d+')
SCENARIO_DIR = Path(__file__).resolve().parents[2] / 'scenarios'
REPO = Path(__file__).resolve().parents[5]
# simulation/tools/bundle.py's SLUG: scenario names and marks.
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
MAX_DURATION_MS = 600_000
MAX_DESCRIPTION = 120
MAX_MARK = 40


@dataclass(frozen=True)
class Signal:
    owner: str  # 'driver' or 'rest_of_car': the scenario dataclass it fills
    kind: str  # 'number', 'bool' or 'name'
    unit: str  # what the value means, quoted when it is out of range
    lo: float | None = None
    hi: float | None = None


# Keys are the Driver and RestOfCar fields, so a file, /driver, /rest_of_car
# and trace.csv share one vocabulary. The number ranges are the C ABI's,
# except steering, which the rig clamps instead.
SIGNALS = {
    'accelerator': Signal('driver', 'number', 'a fraction of pedal travel: 0.5 is half pedal',
                          0.0, 1.0),
    'brake_psi': Signal('driver', 'number',
                        'line pressure in psi, front and rear alike; the sensors read 0..3000',
                        0.0, 3000.0),
    'steering_deg': Signal('driver', 'number', 'steering-wheel degrees; the sensor saturates at '
                           'about -188.9 and +180.6', -180.0, 180.0),
    'rtd_pressed': Signal('driver', 'bool', 'the RTD toggle switch'),
    'hvc_state': Signal('rest_of_car', 'name', 'the HVC state, an lhre HvcState name'),
    'contactors_closed': Signal('rest_of_car', 'bool', 'both AIRs'),
    'shutdown_loop_v': Signal('rest_of_car', 'number', 'volts at both shutdown sense taps: 24.0 '
                              'is closed, 0.0 open', 0.0, 27.0),
}
TOP_KEYS = ('schema', 'description', 'duration_ms', 'setup', 'allow', 'at')
SETUP_KEYS = ('event_mode', 'soc_start')
BLOCK_KEYS = ('t_ms', 'mark', *SIGNALS)
# Keys later stages are expected to add (README, Scenarios). v1 rejects
# them like any unknown key, so an old harness never misreads a newer file.
PLANNED = {
    'brake_front_psi': 'planned: separate front and rear brake; v1 has brake_psi for both',
    'brake_rear_psi': 'planned: separate front and rear brake; v1 has brake_psi for both',
    'after': 'planned: times relative to a firmware state, stage 4',
    'delay_ms': 'planned: times relative to a firmware state, stage 4',
    'expect': 'planned: expectations checked against the run, stage 4',
    'can': 'planned: CAN drops, delays and overrides, stage 6',
}
# The warnings check.py can raise, which allow may name (check.RULES).
WARNINGS = ('brake_near_threshold', 'brake_high', 'pedal_in_deadzone', 'loop_near_threshold',
            'brake_over_throttle', 'rtd_at_boot', 'rtd_too_short', 'rtd_without_brake',
            'rtd_with_pedal', 'rtd_before_ready', 'hvc_contactors_disagree', 'unused_allow')


@dataclass(frozen=True)
class Problem:
    level: str  # 'error' or 'warning'
    code: str
    where: str  # at[2].brake_psi, setup.soc_start, at 1002 ms; '' for the file
    message: str
    line: int | None = None
    t_ms: int | None = None

    def format(self, display: str | None) -> str:
        """One line in bundle.py's style, led by file:line: so an editor's
        terminal opens it. Without a file it is a lookup problem."""
        if display is None:
            return f'vcusil: {self.message} [{self.code}]'
        place = f'{display}:{self.line}' if self.line else display
        where = f'{self.where}: ' if self.where else ''
        return f'{place}: {self.level}: {where}{self.message} [{self.code}]'


def _count(n: int, noun: str) -> str:
    return f'{n} {noun}{"" if n == 1 else "s"}'


class ScenarioError(ValueError):
    """The file cannot run. Every problem found is in .problems."""

    def __init__(self, problems: list[Problem], display: str | None = None):
        # Lineless problems concern the whole file, so they lead.
        self.problems = sorted(problems, key=lambda p: p.line or 0)
        self.display = display
        super().__init__(self.report())

    def report(self) -> str:
        lines = [p.format(self.display) for p in self.problems]
        if self.display is not None:
            lines.append(f'{self.display}: {_count(len(self.problems), "error")}. Nothing ran.')
        return '\n'.join(lines)


@dataclass(frozen=True)
class Setup:
    """Run conditions from setup; None where the file leaves them out."""
    event_mode: int | None = None
    soc_start: float | None = None


@dataclass(frozen=True)
class Ramp:
    to: float
    ramp_ms: int


@dataclass(frozen=True)
class Block:
    index: int
    t_ms: int
    mark: str | None
    values: dict[str, object]  # signal -> value (hvc_state as its number) or Ramp
    line: int | None


@dataclass
class _Lines:
    """The line each key and each value starts on, by its path from the top:
    ('at', 2, 'accelerator', 'to'). A problem with a key is reported at the
    key, one with a value at the value. A block starts at its - line."""
    keys: dict[tuple, int] = field(default_factory=dict)
    values: dict[tuple, int] = field(default_factory=dict)

    def key(self, *path) -> int | None:
        return self.keys.get(path)

    def value(self, *path) -> int | None:
        return self.values.get(path)

    def block(self, index: int, key: str | None = None) -> int | None:
        """The value of key in block index, or the block's own line."""
        return self.values.get(('at', index, key)) or self.values.get(('at', index))


# The plain scalars that are not text: YAML 1.2's core schema without its
# 0x and 0o integers, which, like YAML 1.1's yes, on, 1_000 and dates, are
# text. A quoted scalar is always text.
_BOOL = re.compile(r'(?:true|True|TRUE|false|False|FALSE)\Z')
_NULL = re.compile(r'(?:~|null|Null|NULL|)\Z')
_INT = re.compile(r'[-+]?(?:0|[1-9][0-9]*)\Z')
_FLOAT = re.compile(r'(?:[-+]?(?:(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?'
                    r'|\.(?:inf|Inf|INF))|\.(?:nan|NaN|NAN))\Z')
# Refused: YAML 1.1 reads 010 as 8, YAML 1.2 as 10, and some readers as text.
_LEADING_ZERO = re.compile(r'[-+]?0[0-9]')
_STR = 'tag:yaml.org,2002:str'
_CUT = re.compile(r' +#\S')  # pedal #2, which ends plain text at pedal
_HASH_FIRST = re.compile(r' *:[ \t]+#\S')  # description: #2, which leaves no text
_MAX_DEPTH = 100  # far past the 4 a scenario nests, well inside Python's recursion limit


class _Refused(yaml.YAMLError):
    """A construct the loader does not read, at mark."""

    def __init__(self, mark: yaml.Mark, message: str):
        super().__init__(message)
        self.mark, self.message = mark, message


class _Loader(yaml.SafeLoader):
    """SafeLoader with its YAML 1.1 implicit types replaced, used only to
    compose nodes. Anchors, aliases, tags, directives and ? keys stop the
    load: each block is written out, so the file reads the same without
    them. A list item keeps its - in dash_mark: a block is reported there,
    which may be a line above its first key."""
    yaml_implicit_resolvers: dict = {}
    _depth = 0
    _dash = None  # the - of the list item about to be composed

    def compose_document(self):
        event = self.peek_event()
        if event.version or event.tags:
            # %YAML 1.1 asks for the types this loader reads as text.
            raise _Refused(event.start_mark, _directive('YAML' if event.version else 'TAG'))
        return super().compose_document()

    def scan_yaml_directive_number(self, start_mark):
        try:
            return super().scan_yaml_directive_number(start_mark)
        except ValueError:  # int()'s digit limit, which PyYAML does not catch
            raise _Refused(start_mark, _directive('YAML')) from None

    def scan_flow_scalar_non_spaces(self, double, start_mark):
        """PyYAML hands an 8-digit \\U escape to chr() unchecked, so one past
        U+10FFFF raises ValueError or OverflowError, not a YAML error. Only
        that chr() can raise either: every other code is two or four digits."""
        try:
            return super().scan_flow_scalar_non_spaces(double, start_mark)
        except (ValueError, OverflowError):
            # The reader stopped on the digits, two past the backslash.
            at = self.get_mark()
            escape = yaml.Mark(at.name, at.index - 2, at.line, at.column - 2, at.buffer,
                               at.pointer - 2 if at.pointer is not None else None)
            raise yaml.scanner.ScannerError('while scanning a double-quoted scalar', start_mark,
                                            f'found escape \\U{self.prefix(8)}',
                                            escape) from None

    def parse_block_sequence_entry(self):
        if self.check_token(yaml.BlockEntryToken):
            self._dash = self.peek_token().start_mark
        return super().parse_block_sequence_entry()

    def parse_indentless_sequence_entry(self):
        if self.check_token(yaml.BlockEntryToken):
            self._dash = self.peek_token().start_mark
        return super().parse_indentless_sequence_entry()

    def compose_node(self, parent, index):
        dash, self._dash = self._dash, None
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent):
            raise _Refused(event.start_mark, f'*{event.anchor} is an alias; this loader reads no '
                           'anchors or aliases, so write the block out')
        if event.anchor is not None:
            raise _Refused(event.start_mark, f'&{event.anchor} is an anchor; this loader reads no '
                           'anchors or aliases, so write each block out')
        if event.tag is not None:
            # A tag's %1B decodes to ESC, so one that does not print is escaped.
            tag = event.tag.replace('tag:yaml.org,2002:', '!!')
            tag = tag if tag.isprintable() else _quoted(tag)
            raise _Refused(event.start_mark, f'{tag} is a tag; this loader reads no tags: write '
                           'the value plain, and text in quotes if it must be')
        if self._depth == _MAX_DEPTH:
            raise _Refused(event.start_mark, f'the values are nested more than {_MAX_DEPTH} '
                           'levels deep')
        self._depth += 1
        try:
            node = super().compose_node(parent, index)
        finally:
            self._depth -= 1
        if dash is not None:
            node.dash_mark = dash
        return node

    def fetch_key(self):
        """The scanner's ? indicator: a simple key is fetched without it."""
        raise _Refused(self.get_mark(), '? starts an explicit key, which this loader does not '
                       'read; write key: value')


for _tag, _pattern in (('bool', _BOOL), ('null', _NULL), ('int', _INT), ('float', _FLOAT)):
    _Loader.add_implicit_resolver(f'tag:yaml.org,2002:{_tag}', _pattern, None)


def _directive(name: str) -> str:
    return f'%{name} is a directive, which this loader does not read; delete the line'


def _where(path: tuple) -> str:
    """('at', 2, 'mark') as at[2].mark."""
    return ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in path).removeprefix('.')


def _is_text(path: tuple) -> bool:
    """Whether path is where text goes: the description, a mark, a reason."""
    return (path == ('description',) or (len(path) == 2 and path[0] == 'allow')
            or (len(path) == 3 and path[0] == 'at' and path[2] == 'mark'))


class _Document:
    """Composed nodes as dicts, lists and scalars, with the line of every key
    and value, and the problems that stop the load before the checks."""

    def __init__(self, text: str):
        self.text = text
        self.lines = _Lines()
        self.problems: list[Problem] = []

    def refuse(self, path: tuple, message: str, mark: yaml.Mark) -> None:
        self.problems.append(Problem('error', 'yaml_syntax', _where(path), message,
                                     mark.line + 1))

    def value(self, node: yaml.Node, path: tuple) -> object:
        self.lines.values[path] = getattr(node, 'dash_mark', node.start_mark).line + 1
        if isinstance(node, yaml.MappingNode):
            return self._mapping(node, path)
        if isinstance(node, yaml.SequenceNode):
            return [self.value(item, (*path, i)) for i, item in enumerate(node.value)]
        return self._scalar(node, path)

    def _mapping(self, node: yaml.MappingNode, path: tuple) -> dict:
        found: dict = {}
        for key_node, value_node in node.value:
            key = self._key(key_node, path)
            if key is None:
                continue
            line = key_node.start_mark.line + 1
            if key in found:
                # yaml.safe_load keeps the last one, so another tool would
                # run a different drive.
                hint = ('each moment needs its own block' if len(path) == 2 and path[0] == 'at'
                        else 'merge them' if isinstance(value_node, yaml.CollectionNode)
                        else 'keep one')
                self.refuse((*path, key), f'is written twice, at lines '
                            f'{self.lines.keys[(*path, key)]} and {line}; {hint}',
                            key_node.start_mark)
                continue
            self.lines.keys[(*path, key)] = line
            if (_is_text((*path, key)) and path[:1] != ('at',)
                    and isinstance(value_node, yaml.ScalarNode) and not value_node.style
                    and value_node.value == ''
                    and _HASH_FIRST.match(self.text, key_node.end_mark.index)):
                # description: #2 of the series: all of it is a comment.
                self.refuse((*path, key), 'is empty because its text starts at a " #", which '
                            'starts a comment; put the whole text in quotes', key_node.start_mark)
                continue
            found[key] = self.value(value_node, (*path, key))
        return found

    def _key(self, node: yaml.Node, path: tuple) -> str | None:
        """A key's text: a key is a plain scalar that reads as text."""
        source = self.text[node.start_mark.index:node.end_mark.index]
        if not isinstance(node, yaml.ScalarNode):
            message = f'{source} cannot be a key: a key is a name, such as t_ms'
        elif node.style:
            message = f'the key {source} is in quotes; write it bare'
        elif node.tag != _STR:
            kind = {'bool': 'true or false', 'null': 'null'}.get(node.tag.rsplit(':', 1)[1],
                                                               'a number')
            message = f'the key {source} reads as {kind}; a key is a name, such as t_ms'
        elif node.value == '<<':
            message = '<< is a merge key, which this loader does not read; write each key out'
        else:
            return node.value
        self.refuse(path, message, node.start_mark)
        return None

    def _scalar(self, node: yaml.ScalarNode, path: tuple) -> object:
        text, kind = node.value, node.tag.rsplit(':', 1)[1]
        if kind == 'str':
            if not node.style and _is_text(path) and _CUT.match(self.text, node.end_mark.index):
                # pedal #2: yaml.safe_load also stops at the #, so the file
                # would say less than its writer meant. '  # why' still passes.
                # No mark holds a #, so there it is a comment missing its space.
                fix = ('a mark has no #, so if the rest is a comment, write a space after the #'
                       if path[0] == 'at' else 'put the whole text in quotes')
                self.refuse(path, f'{_yaml(text)} ends at a " #", which starts a comment; {fix}',
                            node.start_mark)
            elif (node.style in ('>', '|') and _is_text(path) and text.count('\n') == 1
                  and text.endswith('\n')):
                # > and | keep the last line break (clip), so one line of text
                # is two lines to every reader.
                self.refuse(path, f'{node.style} keeps the line break at the end of the text; '
                            f'write {node.style}- to drop it, or put the text on the line of its '
                            'key', node.start_mark)
            return text
        if kind == 'null':
            return None
        if kind == 'bool':
            return text.lower() == 'true'
        if _LEADING_ZERO.match(text):
            fixed = (f'it in quotes, "{text}"' if _is_text(path)
                     else re.sub(r'^([-+]?)0+(?=[0-9])', r'\1', text))
            self.refuse(path, f'{text} has a leading zero, which YAML readers disagree on (to '
                        f'some, 010 is 8); write {fixed}', node.start_mark)
            return None
        if kind == 'float':
            # Python spells YAML's .inf and .nan without the dot.
            return float(text.replace('.', '') if text[-1].isalpha() else text)
        try:
            return int(text)
        except ValueError:  # int()'s digit limit
            self.refuse(path, f'an integer of {len(text.lstrip("+-"))} digits is more than '
                        f'Python reads ({sys.get_int_max_str_digits()})', node.start_mark)
            return None


@dataclass(frozen=True)
class ScenarioFile:
    name: str
    path: Path | None  # absolute; None for text that came from no file
    display: str  # repo-relative inside the repo, else as given
    repo_path: str | None  # the path from the repo root, when it is inside
    data: bytes
    sha256: str
    schema: str
    description: str
    setup: Setup
    allow: dict[str, str]
    blocks: tuple[Block, ...]
    timeline: Timeline
    document: dict = field(repr=False)  # the normalised file: parameters()
    lines: _Lines = field(repr=False, compare=False, default_factory=_Lines)

    @property
    def duration_ms(self) -> int:
        return self.timeline.duration_ms

    @property
    def marks(self) -> tuple[tuple[str, int, int], ...]:
        """(mark, t_ms, the sample it is reported at: the first at or after
        t_ms, which for a ramp still shows the value before it)."""
        return tuple((b.mark, b.t_ms, first_step_ms(b.t_ms)) for b in self.blocks if b.mark)

    def parameters(self) -> dict:
        """The file as parsed: numbers as floats, names as written, ramps as
        {to, ramp_ms}, setup with only the keys given. JSON-serialisable."""
        return copy.deepcopy(self.document)


def first_step_ms(t_ms: int) -> int:
    """The first host sample at or after t_ms: the host samples at 3k."""
    return -(-t_ms // STEP_MS) * STEP_MS


def last_step_ms(duration_ms: int) -> int:
    """The last sample of a run: it has ceil(duration_ms / 3) steps."""
    return (-(-duration_ms // STEP_MS) - 1) * STEP_MS


def scenario_paths() -> list[Path]:
    """The files in scenarios/, in names() order. pathlib's * also matches
    dot-files, such as macOS's ._x.yml and Emacs's .#x.yml lock links,
    which are not scenarios."""
    return sorted((p for p in SCENARIO_DIR.glob('*.yml')
                   if p.is_file() and not p.name.startswith('.')), key=lambda p: p.stem)


def misnamed_paths() -> list[Path]:
    """Files in scenarios/ that were meant as scenarios but never load:
    .yaml (YAML's other extension) and .toml (the format before YAML)."""
    return sorted(p for suffix in ('*.yaml', '*.toml') for p in SCENARIO_DIR.glob(suffix)
                  if p.is_file() and not p.name.startswith('.'))


def names() -> list[str]:
    return [p.stem for p in scenario_paths()]


def _slug(text: str, fallback: str = 'my-scenario') -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or fallback


def _name_on_disk(path: Path) -> str:
    """path's file name as its directory lists it. A case-insensitive
    filesystem (macOS) finds LAUNCH.yml when the file is launch.yml."""
    try:
        entries = os.listdir(path.parent)
    except OSError:
        return path.name
    if path.name in entries:
        return path.name
    return next((e for e in entries if e.lower() == path.name.lower()), path.name)


def resolve(arg: str) -> Path:
    """A name from scenarios/, or a path to a .yml file (one with a / or
    ending in .yml). Raises ScenarioError (not_found, bad_name)."""
    if '/' in arg or arg.endswith('.yml'):
        path = Path(arg)
        if not path.is_file():
            # The usual slip: launch.yml, or scenarios/launch, from another
            # directory, when the name alone would do.
            known = names()
            stem = path.stem.lower()
            close = [stem] if stem in known else difflib.get_close_matches(stem, known, n=1)
            hint = (f'; did you mean {close[0]}? scenarios/ has: {", ".join(known)}.' if close
                    else '')
            raise ScenarioError([Problem('error', 'not_found', '',
                                         f'no scenario file at {arg}{hint}')])
        on_disk = Path(_name_on_disk(path))
        if on_disk.suffix != '.yml' or not NAME.fullmatch(on_disk.stem):
            which = 'it' if on_disk.name == path.name else on_disk.name
            raise ScenarioError([Problem(
                'error', 'bad_name', '',
                'a scenario file is named in lowercase words joined by dashes and ends in '
                f'.yml; rename {which} to {_slug(on_disk.stem)}.yml')], arg)
        if on_disk.name != path.name:
            # The file's name is fine: only the argument is in another case,
            # which a case-sensitive filesystem (Linux, CI) would not find.
            raise ScenarioError([Problem(
                'error', 'bad_name', '',
                f'the file is named {on_disk.name}, and only {arg} is in another case; write '
                f'{path.with_name(on_disk.name)}')], arg)
        return path
    if not NAME.fullmatch(arg):
        if Path(arg).suffix:
            hint = f'; a scenario file ends in .yml: rename it to {_slug(Path(arg).stem)}.yml'
        elif _slug(arg) in names():
            hint = f'; did you mean {_slug(arg)}?'
        else:
            hint = ''
        raise ScenarioError([Problem(
            'error', 'bad_name', '',
            f'{arg!r} is not a scenario name: names are lowercase words joined by dashes, like '
            f'my-launch{hint}')])
    path = SCENARIO_DIR / f'{arg}.yml'
    if not path.is_file():
        misnamed = next((p for p in misnamed_paths() if p.stem == arg), None)
        if misnamed is not None:
            raise ScenarioError([Problem(
                'error', 'bad_name', '', f'scenarios/{misnamed.name} is there, but a scenario '
                f'file ends in .yml; rename it to {arg}.yml')])
        known = names()
        close = difflib.get_close_matches(arg, known, n=1)
        raise ScenarioError([Problem(
            'error', 'not_found', '',
            f'no scenario named {arg!r}' + (f'; did you mean {close[0]}?' if close else '.')
            + f' scenarios/ has: {", ".join(known) or "nothing"}. A path to a .yml file also '
            'works.')])
    return path


def load(arg: str | Path, repo: Path | None = None) -> ScenarioFile:
    """--scenario's argument, checked and compiled. repo (default: this
    checkout) decides how the file's path is shown and recorded."""
    path = resolve(str(arg))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ScenarioError([Problem('error', 'not_found', '',
                                     f'cannot read {arg}: {exc.strerror}')]) from exc
    return loads(data, path.stem, path=path, repo=repo)


def _repo_path(path: Path | None, repo: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to((repo or REPO).resolve()).as_posix()
    except ValueError:
        return None


def loads(data: bytes, name: str, path: str | Path | None = None,
          repo: Path | None = None) -> ScenarioFile:
    """A scenario from its bytes. name is the file's stem; path (optional)
    is where it came from, shown as given unless it is inside repo."""
    if not NAME.fullmatch(name):
        raise ScenarioError([Problem('error', 'bad_name', '',
                                     f'{name!r} is not a scenario name; rename it to '
                                     f'{_slug(name)}')])
    absolute = Path(path).resolve() if path is not None else None
    repo_path = _repo_path(absolute, repo)
    display = repo_path or (str(path) if path is not None else f'{name}.yml')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        line = data[:exc.start].count(b'\n') + 1
        raise ScenarioError([Problem('error', 'yaml_syntax', '',
                                     f'byte {exc.start} is not UTF-8 text; save the file as '
                                     'UTF-8', line)], display) from exc
    if text.startswith('\ufeff'):
        # Refused rather than skipped, so a file has one form, as eol=lf in
        # .gitattributes gives it one line ending.
        raise ScenarioError([Problem('error', 'yaml_syntax', '',
                                     'the file starts with a byte-order mark (some Windows '
                                     'editors add one); save it as UTF-8 without a BOM', 1)],
                            display)
    breaks = re.search('[\x85\u2028\u2029]|\r(?!\n)', text)
    if breaks:
        # PyYAML ends a line at each, even in a comment. YAML 1.2 readers do
        # not end one at U+0085, U+2028 or U+2029, and git and grep do not at
        # any of them, so every later line number would disagree. No value
        # may hold one either, so the fix is to delete it.
        n = ord(breaks[0])
        message = ('a carriage return without a line feed ends a line for YAML and not for '
                   'git or grep; save the file with LF line endings' if n == 0x0D
                   else f'U+{n:04X} ends a line for some YAML readers and not others; delete '
                        'it')
        raise ScenarioError([Problem('error', 'yaml_syntax', '', message,
                                     text.count('\n', 0, breaks.start()) + 1)], display)
    try:
        root = yaml.compose(text, Loader=_Loader)
    except _Refused as exc:
        raise ScenarioError([Problem('error', 'yaml_syntax', '', exc.message,
                                     exc.mark.line + 1)], display) from exc
    except yaml.reader.ReaderError as exc:
        raise ScenarioError([Problem('error', 'yaml_syntax', '',
                                     f'U+{exc.character:04X} is a character YAML does not '
                                     'allow; delete it', text.count('\n', 0, exc.position) + 1)],
                            display) from exc
    except yaml.MarkedYAMLError as exc:
        raise ScenarioError([_syntax_problem(exc, text)], display) from exc
    built = _Document(text)
    # Empty, comment-only or --- alone; null or ~ is a value like 7000.
    empty = root is None or (isinstance(root, yaml.ScalarNode) and not root.style
                             and root.value == '')
    doc = None if empty else built.value(root, ())
    if not built.problems and not empty and not isinstance(doc, dict):
        what = ('a list' if isinstance(doc, list) else 'text' if isinstance(doc, str)
                else _yaml(doc))
        built.refuse((), f'the top level is {what}, not keys; a scenario file is keys, each at '
                     f'the start of a line, such as schema: {SCHEMA}', root.start_mark)
    if built.problems:
        raise ScenarioError(built.problems, display)
    checker = _Checker(built.lines)
    checked = checker.check(doc or {})
    if checked is None or checker.problems:
        raise ScenarioError(checker.problems, display)
    description, duration, setup, allow, blocks, document = checked
    checker.timing(duration, blocks)
    if checker.problems:
        raise ScenarioError(checker.problems, display)
    return ScenarioFile(
        name=name, path=absolute, display=display, repo_path=repo_path, data=data,
        sha256=hashlib.sha256(data).hexdigest(), schema=SCHEMA, description=description,
        setup=setup, allow=allow, blocks=blocks, timeline=_compile(name, duration, blocks),
        document=document, lines=built.lines)


_DASH = re.compile(r'\s*-\S')  # -t_ms: 0, --t_ms: 0: a block's dash with no space after it
_GLUED = re.compile(r'\s*(?:- )?[\w-]+:[^\s:]')  # t_ms:200
_OWN_KEY = re.compile(r' *(?:- +)?([\w-]+):')  # the key a row starts with
# Keys whose value is free text, where a ': ' or an opener may be meant.
_TEXT_KEYS = ('description', 'mark', *WARNINGS)
_NOT_TEXT = (*SIGNALS, *SETUP_KEYS, 'schema', 'duration_ms', 't_ms', 'setup', 'allow', 'at')


def _char(c: str) -> str:
    """c as U+001B, or as U+200B ZERO WIDTH SPACE where it has a name."""
    return f'U+{ord(c):04X} {unicodedata.name(c, "")}'.rstrip()


def _hidden_space(row: str) -> int | None:
    """The column where row has a no-break space or a character that does
    not show in place of YAML's space: in the indent, after a dash or after
    a key's colon. Any editor shows the line as if it were right."""
    for pattern in (r' *', r' *-', r' *(?:- +)?[\w-]+:'):
        found = re.match(pattern, row)
        if found and found.end() < len(row):
            c = row[found.end()]
            if not c.isascii() and (c.isspace() or not c.isprintable()):
                return found.end()
    return None


def _open_quote(text: str, mark: yaml.Mark) -> yaml.Mark | None:
    """The start of a quoted text that runs from an earlier line onto mark's:
    a quote left open, which the next ' or " closes, even one in a comment."""
    last = None
    try:
        for token in yaml.scan(text, Loader=_Loader):
            if token.start_mark.index >= mark.index:
                break
            if isinstance(token, yaml.ScalarToken) and token.style in ('"', "'"):
                last = token
    except yaml.YAMLError:
        pass
    if last is not None and last.start_mark.line < last.end_mark.line == mark.line:
        return last.start_mark
    return None


def _keyless(text: str, mark: yaml.Mark) -> yaml.Mark | None:
    """The start of a mapping's first key written without its :, as in
    "- t_ms 600". PyYAML cannot know a key was meant, so it reads it as text,
    with the next line if that is indented further, and fails on the line
    after the slip."""
    last = None
    try:
        for token in yaml.scan(text, Loader=_Loader):
            if token.start_mark.index >= mark.index:
                break
            last = token
    except yaml.YAMLError:
        pass
    if not (isinstance(last, yaml.ScalarToken) and last.plain
            and last.start_mark.line < mark.line and re.match(r'[\w-]+ ', last.value)):
        return None
    start = last.start_mark
    row = text[text.rfind('\n', 0, start.index) + 1:start.index]
    return start if re.fullmatch(r' *(?:- +)?', row) else None


def _dedented(rows: list[str], above: int | None) -> tuple[int, int] | None:
    """(the column, the block keys' column) when the row above is a block's
    key written left of its block, as "mark:" at the dashes' column of an
    indentless list ("- t_ms: 200" at the margin): it leaves the block."""
    if above is None:
        return None
    own = re.match(r'( *)([\w-]+):', rows[above])
    dash = next((re.match(r' *- +', rows[n]) for n in range(above - 1, -1, -1)
                 if re.match(r' *- +', rows[n])), None)
    if own is None or own[2] not in BLOCK_KEYS or dash is None:
        return None
    return (len(own[1]), dash.end()) if len(own[1]) < dash.end() else None


def _syntax_problem(exc: yaml.MarkedYAMLError, text: str) -> Problem:
    """PyYAML's message, at the line of the slip, with a hint for the usual
    ones. Where PyYAML gives up after an opener it never saw closed, or a key
    it found no : for, the slip is at the opener or the key."""
    rows = re.split(r'\r\n|[\r\n]', text)
    mark = exc.problem_mark
    line, message = mark.line, f'YAML: {exc.problem} at column {mark.column + 1}'
    row = rows[mark.line] if mark.line < len(rows) else ''
    start = exc.context_mark
    opened = rows[start.line][start.column:] if start and start.line < len(rows) else ''
    above = next((n for n in range(mark.line - 1, -1, -1)
                  if rows[n].strip() and not rows[n].lstrip().startswith('#')), None)
    above_row = rows[above] if above is not None else ''
    hidden = next(((n, column) for n in range(min(mark.line, len(rows) - 1), -1, -1)
                   if (column := _hidden_space(rows[n])) is not None), None)
    quote = _open_quote(text, mark)
    keyless = (_keyless(text, mark) if exc.problem == 'mapping values are not allowed here'
               or exc.problem.endswith("'<block mapping start>'") else None)
    hint = None
    if exc.problem == 'but found another document':
        message = 'a second document starts here; a scenario file is one document'
    elif hidden is not None:
        line, column = hidden
        c = rows[line][column]
        message = (f'YAML: column {column + 1} holds {_char(c)}, which YAML does not read as a '
                   'space' if c.isspace() else f'YAML: column {column + 1} holds {_char(c)}, '
                                               'which does not show')
        hint = 'retype it as a space' if c.isspace() else 'delete it'
    elif quote is not None:
        line, message = quote.line, (f'YAML: the quote at column {quote.column + 1} is not '
                                     'closed on its line')
        hint = 'end the text with the same quote'
    elif exc.context == 'while scanning a quoted scalar' and 'end of stream' in exc.problem:
        line, message = start.line, f'YAML: the quote at column {start.column + 1} is never closed'
        hint = 'end the text with the same quote'
    elif exc.context in ('while parsing a flow mapping', 'while parsing a flow sequence'):
        before_comment = opened.split(' #')[0]
        opener, closer = opened[0], '}' if opened[0] == '{' else ']'
        if before_comment.count(opener) > before_comment.count(closer):
            line, message = start.line, (f'YAML: the {opener} at column {start.column + 1} is '
                                         'not closed on its line')
            hint = (f'a # after a space starts a comment, which hides the rest of the line: '
                    f'close the {opener} before it' if ' #' in opened
                    else f'close it with {closer} on the same line')
        elif opener == '{':
            hint = 'separate the keys with a comma: {to: 0.4, ramp_ms: 300}'
    elif exc.context == 'while scanning a simple key':
        line, message = start.line, f'YAML: the key at column {start.column + 1} has no : after it'
        hint = ('write a space after the :' if re.search(r'[^\s:]:[^\s:]', rows[start.line])
                else 'write key: value, with a space after the :')
    elif keyless is not None:
        line, message = keyless.line, f'YAML: the key at column {keyless.column + 1} has no : after it'
        hint = 'write key: value, with a space after the :'
    elif exc.problem == 'mapping values are not allowed here':
        before, own = row[:mark.column], _OWN_KEY.match(row)
        word = re.search(r'([\w-]+)$', before)
        dedented = _dedented(rows, above)
        if own and _GLUED.match(row):
            # accelerator:{to: 0.6, ...}: the glued key swallows the {.
            hint = f'write a space after {own[1]}:'
        elif ': ' in before:
            if re.search(r', *[\w-]+$', before):
                hint = 'a comma separates keys only inside { }: put each key on its own line'
            elif (own and own[1] not in _TEXT_KEYS and word
                  and word[1] in (*TOP_KEYS, *SETUP_KEYS, *BLOCK_KEYS, 'ramp_ms')):
                hint = f'{word[1]} is a key of its own: put each key on its own line'
            else:
                hint = "a ': ' inside text starts a key: put the text in quotes"
        elif dedented is not None:
            column, keys = dedented
            line, message = above, (f'YAML: the key at column {column + 1} is left of its '
                                    "block's other keys")
            hint = f'line it up with the t_ms above it, at column {keys + 1}'
        elif _DASH.match(above_row):
            hint = f'write a space after the - on line {above + 1}: - t_ms: 0'
        elif _GLUED.match(above_row):
            hint = f'write a space after the : on line {above + 1}'
        else:
            hint = 'indent this key like the keys above it'
    elif exc.problem.startswith("found character '\\t'"):
        hint = 'YAML indents with spaces: replace the tab with spaces'
    elif exc.problem.startswith('found unknown escape character'):
        hint = ('in double quotes a \\ starts an escape: write \\\\ for a backslash, or put the '
                'text in single quotes')
    elif exc.problem.startswith('found escape \\U'):
        hint = 'no character has that code, since Unicode ends at U+10FFFF: check the digits'
    elif exc.problem.startswith('found character'):
        hint = f'text that starts with {row[mark.column]} goes in quotes'
    elif exc.problem.startswith('expected <block end>'):
        key = re.match(r' *([\w-]+):(?: |$)', row)
        if _DASH.match(row):
            hint = 'a block starts with a dash and a space: - t_ms: 0'
        elif _DASH.match(above_row):
            hint = f'write a space after the - on line {above + 1}: - t_ms: 0'
        elif _GLUED.match(above_row):  # with a comment line between
            hint = f'write a space after the : on line {above + 1}'
        elif exc.problem.endswith("'?'") and key:
            # A key at the column of the list's dashes ends the list.
            hint = ('a block starts with a dash: - t_ms: N' if key[1] == 't_ms'
                    else "line a block's keys up with its t_ms, two spaces past the -"
                    if key[1] in BLOCK_KEYS else 'top-level keys start at the left margin')
        elif exc.problem.endswith("'<block mapping start>'"):
            # Not for '<scalar>': text after a value, which the hint below
            # may explain.
            hint = 'indent this line like the lines above it'
    row = rows[line] if line < len(rows) else ''
    value = re.match(r' *(?:- +)?([\w-]+): +([\[{*|>]|-(?= ))', row)
    if hint is None and value and value[1] == 'at' and value[2] == '-':
        hint = 'put the first block on its own line under at:'
    elif hint is None and value and value[1] not in _NOT_TEXT:
        # For text only: a [ or { after a signal is a ramp or list slip.
        hint = f'text that starts with {value[2]} goes in quotes'
    return Problem('error', 'yaml_syntax', '', _hinted(message, hint), line + 1)


def _plain(text: str) -> bool:
    """Whether text, written bare, reads back as the same text here."""
    try:
        node = yaml.compose(text, Loader=_Loader)
    except yaml.YAMLError:
        return False
    return (isinstance(node, yaml.ScalarNode) and not node.style and node.tag == _STR
            and node.value == text)


_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t', '\r': '\\r'}


def _quoted(text: str) -> str:
    """text in double quotes, with what does not print escaped, as YAML
    writes it: a message never carries an ESC or a CR to the terminal."""
    def escaped(c: str) -> str:
        n = ord(c)
        if c in _ESCAPES:
            return _ESCAPES[c]
        if c.isprintable():
            return c
        return f'\\x{n:02x}' if n < 0x100 else f'\\u{n:04x}' if n < 0x10000 else f'\\U{n:08x}'
    return '"' + ''.join(map(escaped, text)) + '"'


def _yaml(value: object) -> str:
    """A value as YAML writes it, for messages: text in double quotes only
    where, bare, it would read as something else or not show."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, float) and math.isinf(value):
        return '.inf' if value > 0 else '-.inf'
    if isinstance(value, float) and math.isnan(value):
        return '.nan'
    if isinstance(value, str):
        return value if value.isprintable() and _plain(value) else _quoted(value)
    if isinstance(value, dict):
        return '{' + ', '.join(f'{_yaml(k)}: {_yaml(v)}' for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_yaml(v) for v in value) + ']'
    return repr(value)


# YAML 1.1's booleans and their spellings here; this loader reads the words
# as text.
_BOOL_WORDS = {'yes': True, 'y': True, 'on': True, 'true': True,
               'no': False, 'n': False, 'off': False, 'false': False}


def _reads_as(text: str, kind: str) -> bool:
    """Whether text, written bare, reads here as kind: 'ms' or 'number'."""
    if _LEADING_ZERO.match(text):
        return False
    return bool(_INT.match(text) or (kind == 'number' and _FLOAT.match(text)))


def _respelt(value: object, kind: str) -> str | None:
    """For text where kind ('bool', 'ms' or 'number') goes: the spelling
    that reads as what it most likely means, as a hint, or None."""
    if not isinstance(value, str):
        return None
    # YAML strips spaces and tabs around a plain value, so what is left is
    # a pasted no-break space or the like; strip() would hide it.
    odd = next((c for c in value if not c.isascii() and (c.isspace() or not c.isprintable())),
               None)
    if odd is not None:
        seen = 'YAML does not read as a space' if odd.isspace() else 'does not show'
        return f'it holds {_char(odd)}, which {seen}; delete it'
    text = value.strip()
    if kind == 'bool':
        word = _BOOL_WORDS.get(text.lower())
        return None if word is None else f'write {_yaml(word)}'
    if _reads_as(text, kind):
        return 'write it without quotes'
    if kind == 'number' and re.fullmatch(r'[-+]?\.?(?:nan|inf|infinity)', text, re.IGNORECASE):
        return 'YAML writes .inf and .nan, which are out of range here anyway'
    unit = re.fullmatch(r'([-+]?[0-9.]+)\s*[^\W\d_]+', text)  # 300ms, 0.5 s
    number = unit[1] if unit else text.replace('_', '')
    if re.fullmatch(r'[-+]?0[xob][0-9a-f]+', number, re.IGNORECASE):
        try:
            number = str(int(number, 0))
        except ValueError:  # 0o9, 0b2
            return None
    if number == text or not _reads_as(number, kind):
        return None
    return f'units go in a # comment: write {number}' if unit else f'write {number}'


def _range(lo: float, hi: float) -> str:
    return f'{lo:g}..{hi:g}'


class _Checker:
    """Pass 1 checks every key on its own; pass 2 (timing) runs only on a
    clean pass 1, so a typo never sets off misleading follow-on errors."""

    def __init__(self, lines: _Lines):
        self.lines = lines
        self.problems: list[Problem] = []

    def add(self, code: str, where: str, message: str, line: int | None) -> None:
        self.problems.append(Problem('error', code, where, message, line))

    def check(self, doc: dict):
        schema = doc.get('schema')
        if isinstance(schema, str) and schema != SCHEMA and _ANY_VERSION.fullmatch(schema):
            # The rest of the file is in a format this harness does not know.
            self.add('wrong_schema', 'schema', f'{_yaml(schema)}: this file was written for a '
                     f'different harness version; this one reads {SCHEMA}',
                     self.lines.value('schema'))
            return None
        document: dict = {}
        for key in doc:
            if key not in TOP_KEYS:
                self._unknown_top(key)
        for key in ('schema', 'description', 'duration_ms', 'at'):
            if key not in doc:
                self._missing_top(key)
        if 'schema' in doc:
            if not isinstance(schema, str):
                self.add('wrong_type', 'schema', f'{_yaml(schema)} is not a string; write '
                         f'schema: {SCHEMA}', self.lines.value('schema'))
            elif schema != SCHEMA:
                self.add('wrong_schema', 'schema', f'{_yaml(schema)} is not {SCHEMA}',
                         self.lines.value('schema'))
        document['schema'] = SCHEMA
        description = self._text(doc, 'description', 'description', self.lines.value(
            'description'), 'say in one line what the drive does', MAX_DESCRIPTION)
        document['description'] = description
        duration = None
        if 'duration_ms' in doc:
            line = self.lines.value('duration_ms')
            duration = self._ms(doc['duration_ms'], 'duration_ms', line)
            if duration is not None and not 1 <= duration <= MAX_DURATION_MS:
                self.add('out_of_range', 'duration_ms', f'{duration} is outside '
                         f'1..{MAX_DURATION_MS} ms (10 minutes)', line)
                duration = None
        document['duration_ms'] = duration
        setup, document['setup'] = self._setup(doc.get('setup'))
        allow = self._allow(doc.get('allow'))
        document['allow'] = dict(allow)
        blocks, document['at'] = self._blocks(doc['at'], duration) if 'at' in doc else ((), [])
        return description, duration, setup, allow, blocks, document

    def _unknown_top(self, key: str) -> None:
        if key in PLANNED:
            hint = PLANNED[key]
        elif key in SIGNALS:
            hint = 'signals go in a block under at:, at the time they change'
        elif key in SETUP_KEYS:
            hint = f'{key} goes under setup:'
        else:
            hint = _did_you_mean(key, TOP_KEYS)
        self.add('unknown_key', key, _hinted(f'is not an {SCHEMA} key', hint),
                 self.lines.key(key))

    def _missing_top(self, key: str) -> None:
        # A top-level key indented under another is part of that one.
        inside = [path[:-1] for path in self.lines.keys if len(path) > 1 and path[-1] == key]
        hint = ''
        if key == 'at':
            hint = '; a scenario needs at least one block under at:'
        elif inside:
            hint = (f'; the {key} indented under {_where(inside[0])} counts as part of it: '
                    'top-level keys are not indented')
        self.add('missing_key', key, 'is missing' + hint, None)

    def _text(self, table: dict, key: str, where: str, line: int | None, what: str,
              limit: int | None = None) -> str | None:
        if key not in table:
            return None
        value = table[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            self.add('bad_value', where, f'is empty; {what}', line)
        elif not isinstance(value, str):
            self.add('wrong_type', where, f'{_yaml(value)} is not a string; write it in quotes',
                     line)
        elif value.splitlines() != [value]:  # any break str knows, U+2028 too
            self.add('bad_value', where, 'is more than one line; keep it to one and put the '
                     'rest in # comments', line)
        elif hidden := next((c for c in value if c != '\t' and unicodedata.category(c)
                             in ('Cc', 'Cf', 'Cs')), None):
            # "\e[2J" would clear the terminal that check prints it on. A
            # surrogate ("\ud83d\ude80", the JSON way to write an emoji) stays
            # two halves in YAML, which no UTF-8 output can hold.
            what = ('half of a UTF-16 pair, which YAML does not join; write the character '
                    'itself' if unicodedata.category(hidden) == 'Cs'
                    else 'which does not print; delete it')
            self.add('bad_value', where, f'holds {_char(hidden)}, {what}', line)
        elif limit is not None and len(value) > limit:
            self.add('bad_value', where, f'is {len(value)} characters; the limit is {limit} '
                     '(put the rest in # comments)', line)
        else:
            return value
        return None

    def _ms(self, value: object, where: str, line: int | None) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            hint = _respelt(value, 'ms') or 'write an integer, such as 1500'
            self.add('wrong_type', where, f'{_yaml(value)} is not whole milliseconds; {hint}',
                     line)
            return None
        return value

    def _number(self, value: object, where: str, line: int | None, lo: float, hi: float,
                unit: str) -> float | None:
        # A bool is an int to Python, never a number in a scenario.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.add('wrong_type', where, _hinted(f'{_yaml(value)} is not a number ({unit})',
                                                  _respelt(value, 'number')), line)
            return None
        if not lo <= value <= hi:  # also false for nan
            self.add('out_of_range', where, f'{_yaml(value)} is outside {_range(lo, hi)} '
                     f'({unit})', line)
            return None
        return float(value) + 0.0  # an integer becomes a float, -0.0 becomes 0.0

    def _name(self, value: object, key: str, table: dict[str, int], kind: str, where: str,
              line: int | None) -> int | None:
        if not isinstance(value, str):
            named = {v: k for k, v in table.items()}
            example = named.get(value) if type(value) is int else None  # not for a bool
            hint = (f'write the name: {key}: {example}' if example
                    else f'write the name, one of {", ".join(table)}')
            self.add('wrong_type', where, f'{_yaml(value)} is not an lhre {kind} name; {hint}',
                     line)
            return None
        if value in table:
            return table[value]
        upper = {name.lower(): name for name in table}
        if value.lower() in upper:
            hint = f'did you mean {upper[value.lower()]}? names are upper case'
        else:
            hint = _did_you_mean(value.upper(), table) or f'one of {", ".join(table)}'
        self.add('bad_value', where, f'{_yaml(value)} is not an lhre {kind} name; {hint}', line)
        return None

    def _setup(self, setup: object) -> tuple[Setup, dict]:
        if setup is None:  # left out, or setup: with every key commented out
            return Setup(), {}
        if not isinstance(setup, dict):
            self.add('wrong_type', 'setup', f'{_yaml(setup)} is not a mapping; write setup: on '
                     'its own line, then its keys under it, indented',
                     self.lines.value('setup'))
            return Setup(), {}
        written: dict = {}
        event_mode = soc_start = None
        for key, value in setup.items():
            where, line = f'setup.{key}', self.lines.value('setup', key)
            if key == 'event_mode':
                event_mode = self._name(value, key, EVENT_MODES, 'EventMode', where, line)
                if event_mode is not None:
                    written[key] = value
            elif key == 'soc_start':
                soc_start = self._number(value, where, line, 0.0, 1.0,
                                         'the state of charge as a fraction: 0.9 is 90 %')
                if soc_start is not None:
                    written[key] = soc_start
            else:
                if key in TOP_KEYS:
                    hint = _INDENTED.format(table='setup')
                elif key == 'brake_psi_full':
                    hint = ('it stays the --brake-psi-full option: it calibrates the car model, '
                            'not the drive')
                else:
                    hint = _did_you_mean(key, SETUP_KEYS)
                self.add('unknown_key', where, _hinted('is not a setup key', hint),
                         self.lines.key('setup', key))
        return Setup(event_mode, soc_start), written

    def _allow(self, allow: object) -> dict[str, str]:
        if allow is None:  # left out, or allow: with every code commented out
            return {}
        if not isinstance(allow, dict):
            self.add('wrong_type', 'allow', f'{_yaml(allow)} is not a mapping; write allow: on '
                     'its own line, then one code: reason line per warning, indented',
                     self.lines.value('allow'))
            return {}
        allowed = {}
        for code in allow:
            where, line = f'allow.{code}', self.lines.key('allow', code)
            if code == 'unused_allow':
                self.add('unknown_key', where, 'cannot be allowed: it is the warning for an '
                         'allow entry that never fires', line)
                continue
            if code not in WARNINGS:
                hint = (_INDENTED.format(table='allow') if code in TOP_KEYS
                        else _did_you_mean(code, WARNINGS))
                self.add('unknown_key', where, _hinted('is not a warning code', hint), line)
                continue
            text = self._text(allow, code, where, self.lines.value('allow', code),
                              'say why the drive sets it off')
            if text is not None:
                allowed[code] = text
        return allowed

    def _blocks(self, at: object, duration: int | None) -> tuple[tuple[Block, ...], list]:
        line = self.lines.value('at')
        if isinstance(at, dict):
            self.add('wrong_type', 'at', 'is a single mapping; write each block as a list item, '
                     '- t_ms: N, once per moment', line)
            return (), []
        if at in (None, []):
            self.add('missing_key', 'at', 'has no blocks; a scenario needs at least one block '
                     'under at:', line)
            return (), []
        if not isinstance(at, list):
            self.add('wrong_type', 'at', f'{_yaml(at)} is not a list of blocks; write at:, then '
                     'each block as a list item, - t_ms: N, once per moment', line)
            return (), []
        bad = [i for i, block in enumerate(at) if not isinstance(block, dict)]
        for i in bad:
            hint = ('write a space after the :' if isinstance(at[i], str)
                    and re.match(r'[\w-]+:\S', at[i]) else 'a block is - t_ms: N, with its keys '
                    'under it')
            self.add('wrong_type', f'at[{i}]', f'{_yaml(at[i])} is not a block; {hint}',
                     self.lines.block(i))
        if bad:
            return (), []
        blocks, written = [], []
        previous: int | None = None
        marks: dict[str, int] = {}
        for i, block in enumerate(at):
            t, entry, values, mark = self._block(i, block, duration, previous, marks)
            previous = t
            written.append(entry)
            if t is not None:
                blocks.append(Block(i, t, mark, values, self.lines.block(i)))
        return tuple(blocks), written

    def _block(self, i: int, block: dict, duration: int | None, previous: int | None,
               marks: dict[str, int]):
        where = f'at[{i}]'
        entry: dict = {}
        t = None
        if 't_ms' not in block:
            self.add('missing_key', f'{where}.t_ms', 'is missing; every block needs its time',
                     self.lines.block(i))
        else:
            line = self.lines.block(i, 't_ms')
            t = self._ms(block['t_ms'], f'{where}.t_ms', line)
            if t is not None:
                if t < 0:
                    self.add('out_of_range', f'{where}.t_ms', f'{t} is before 0; time starts '
                             'at 0', line)
                elif duration is not None and t >= duration:
                    self.add('out_of_range', f'{where}.t_ms', f'{t} is not before duration_ms '
                             f'({duration}); the last sample is at {last_step_ms(duration)}',
                             line)
                if previous is not None and t == previous:
                    self.add('time_order', f'{where}.t_ms', f'two blocks at {t} ms: merge '
                             'them', line)
                elif previous is not None and t < previous:
                    self.add('time_order', f'{where}.t_ms', f'{t} comes after {previous} '
                             f'(at[{i - 1}]); keep the blocks in time order', line)
                entry['t_ms'] = t
        mark = None
        values: dict[str, object] = {}
        for key, value in block.items():
            if key == 't_ms':
                continue
            key_where, line = f'{where}.{key}', self.lines.block(i, key)
            if key == 'mark':
                mark = self._mark(value, key_where, line, i, marks)
                if mark is not None:
                    entry['mark'] = mark
            elif key in SIGNALS:
                compiled = self._signal(key, value, key_where, ('at', i, key))
                if compiled is None:
                    continue
                values[key] = compiled
                if isinstance(compiled, Ramp):
                    entry[key] = {'to': compiled.to, 'ramp_ms': compiled.ramp_ms}
                else:  # a name as written, a number as a float
                    entry[key] = value if key == 'hvc_state' else compiled
            else:
                self._unknown_in_block(key, key_where, self.lines.key('at', i, key), where)
        if set(block) <= {'t_ms'}:
            self.add('sets_nothing', where, 'sets nothing; give it a signal or a mark, or remove '
                     'it', self.lines.block(i))
        return t, entry, values, mark

    def _mark(self, value: object, where: str, line: int | None, i: int,
              marks: dict[str, int]) -> str | None:
        if not isinstance(value, str):
            # 2026 reads as a number; "2026" is a mark.
            hint = (f'write it in quotes, "{_yaml(value)}"'
                    if value is not None and NAME.fullmatch(_yaml(value))
                    else 'a mark is a name, such as loop-open')
            self.add('wrong_type', where, f'{_yaml(value)} is not a string; {hint}', line)
            return None
        if not NAME.fullmatch(value) or len(value) > MAX_MARK:
            # The cut can end on a dash, which no mark may, and a mark of
            # no letters or digits has no slug at all.
            example = _slug(value, '')[:MAX_MARK].rstrip('-') or 'loop-open'
            self.add('bad_value', where, f'{_yaml(value)} is not a mark name: at most '
                     f'{MAX_MARK} characters of lowercase words joined by dashes, such as '
                     f'{_yaml(example)}', line)
            return None
        if value in marks:
            self.add('bad_value', where, f'{value} is already the mark of at[{marks[value]}]',
                     line)
            return None
        marks[value] = i
        return value

    def _unknown_in_block(self, key: str, where: str, line: int | None, block: str) -> None:
        if key in PLANNED:
            hint = PLANNED[key]
        elif key in TOP_KEYS:
            hint = _INDENTED.format(table=block)
        elif key in SETUP_KEYS:
            hint = f'{key} goes under setup:'
        else:
            hint = _did_you_mean(key, BLOCK_KEYS)
        self.add('unknown_key', where, _hinted(f'is not an {SCHEMA} key', hint), line)

    def _signal(self, key: str, value: object, where: str, path: tuple) -> object | None:
        signal = SIGNALS[key]
        line = self.lines.value(*path)
        if isinstance(value, dict):
            return self._ramp(signal, value, where, path)
        if signal.kind == 'number':
            return self._number(value, where, line, signal.lo, signal.hi, signal.unit)
        if signal.kind == 'bool':
            if not isinstance(value, bool):
                hint = _respelt(value, 'bool') or 'write true or false'
                self.add('wrong_type', where, f'{_yaml(value)} is not true or false; {hint}',
                         line)
                return None
            return value
        return self._name(value, key, HVC_STATES, 'HvcState', where, line)

    def _ramp(self, signal: Signal, ramp: dict, where: str, path: tuple) -> Ramp | None:
        line = self.lines.value(*path)
        if signal.kind == 'bool':
            self.add('bad_ramp', where, 'is a switch and cannot ramp; write true or false', line)
            return None
        if signal.kind != 'number':
            self.add('bad_ramp', where, 'is a state and cannot ramp; write its name, such as '
                     'ENERGIZED', line)
            return None
        extra = [k for k in ramp if k not in ('to', 'ramp_ms')]
        missing = [k for k in ('to', 'ramp_ms') if k not in ramp]
        glued = next((k for k in extra if re.fullmatch(r'[\w-]+:\S+', k)), None)
        if glued:
            # {to:1.0}: without the space, YAML reads one key with no value.
            self.add('bad_ramp', where, f'a ramp is {{to: X, ramp_ms: N}}: {glued} is one key, '
                     'not a key and a value; write a space after each :', line)
            return None
        if extra or missing:
            what = '; '.join([f'{k} is not a ramp key' for k in extra]
                             + [f'it has no {k}' for k in missing])
            self.add('bad_ramp', where, f'a ramp is {{to: X, ramp_ms: N}}: {what}', line)
            return None
        to = self._number(ramp['to'], f'{where}.to', self.lines.value(*path, 'to'), signal.lo,
                          signal.hi, signal.unit)
        ramp_line = self.lines.value(*path, 'ramp_ms')
        ramp_ms = self._ms(ramp['ramp_ms'], f'{where}.ramp_ms', ramp_line)
        if ramp_ms is not None and ramp_ms < 1:
            self.add('bad_ramp', f'{where}.ramp_ms', f'{ramp_ms} is under 1 ms; a ramp takes at '
                     'least 1 ms (write a plain value for a step)', ramp_line)
            return None
        if ramp_ms is not None and ramp_ms > MAX_DURATION_MS:
            # Also keeps t_ms + ramp_ms inside the digits Python will print.
            self.add('bad_ramp', f'{where}.ramp_ms', f'{ramp_ms} is longer than the longest run, '
                     f'{MAX_DURATION_MS} ms', ramp_line)
            return None
        if to is None or ramp_ms is None:
            return None
        return Ramp(to, ramp_ms)

    def timing(self, duration: int, blocks: tuple[Block, ...]) -> None:
        """Pass 2: what the timeline does across blocks, against the 3 ms
        samples the host takes."""
        last = last_step_ms(duration)
        for signal in SIGNALS:
            changes = [b for b in blocks if signal in b.values]
            for n, block in enumerate(changes):
                after = changes[n + 1] if n + 1 < len(changes) else None
                value = block.values[signal]
                where = f'at[{block.index}].{signal}'
                line = self.lines.block(block.index, signal)
                if isinstance(value, Ramp):
                    end = block.t_ms + value.ramp_ms
                    if after is not None and after.t_ms < end:
                        self.add('ramp_overlap', f'at[{after.index}].{signal}',
                                 f'changes at {after.t_ms} ms, inside the ramp of '
                                 f'at[{block.index}], which runs until {end} ms; start this '
                                 f'change at {end} or later, or shorten that ramp',
                                 self.lines.block(after.index, signal))
                        continue
                    if end > duration:
                        self.add('ramp_past_end', where, f'ramps until {end} ms, past '
                                 f'duration_ms ({duration}); shorten the ramp or lengthen the '
                                 'run', line)
                self._sampled(block, after, signal, where, line, last)
        for block in blocks:
            if block.mark:
                self._sampled(block, None, None, f'at[{block.index}].mark',
                              self.lines.block(block.index, 'mark'), last)

    def _sampled(self, block: Block, after: Block | None, signal: str | None, where: str,
                 line: int | None, last: int) -> None:
        """unsampled: no 3 ms sample in [lo, hi] sees what block sets for
        signal (None: its mark). By the Timeline's rules (README, Scenarios)
        a ramp at T still holds the old value at T, so a ramp is first seen
        after T, and a value is still seen at the start of a ramp after it."""
        ramp = signal is not None and isinstance(block.values[signal], Ramp)
        lo = first_step_ms(block.t_ms + 1 if ramp else block.t_ms)
        seen = 'shown' if signal is None else 'sampled'
        first = (f'a ramp starts at the held value, so its first visible sample is the first '
                 f'one after {block.t_ms} ms, at {lo}' if ramp
                 else f'the first sample from {block.t_ms} ms is at {lo}')
        if after is None:
            if lo > last:
                but = f', but {first}' if ramp else ''
                self.add('unsampled', where, f'is never {seen}: the host samples every {STEP_MS} '
                         f'ms and the last sample is at {last} ms{but}; move it earlier, or make '
                         f'duration_ms at least {lo + 1}', line)
            return
        next_ramp = isinstance(after.values[signal], Ramp)
        hi = after.t_ms if next_ramp else after.t_ms - 1
        if lo <= hi:
            return
        when = (f'after the next change at {after.t_ms} (at[{after.index}])' if lo > after.t_ms
                else f'which already sees the next change (at[{after.index}])')
        if next_ramp:
            fix = (f'start that ramp at {lo} ms or later: its first sample still shows the value '
                   'before it')
        elif ramp:
            fix = f'move the next change after {lo} ms'
        else:
            fix = f'hold the value through {lo} ms'
        self.add('unsampled', where, f'is never {seen}: the host samples every {STEP_MS} ms, '
                 f'and {first}, {when}; {fix}', line)


_INDENTED = 'top-level keys are not indented, so this one counts as part of {table}'


def _hinted(message: str, hint: str | None) -> str:
    return f'{message}; {hint}' if hint else message


def _did_you_mean(key: str, choices) -> str | None:
    close = difflib.get_close_matches(key, list(choices), n=1)
    hint = f'did you mean {close[0]}?' if close else None
    odd = _not_ascii(key)
    return f'{hint} {odd}' if hint and odd else hint or odd


def _not_ascii(text: str) -> str | None:
    """Keys and names are ASCII. A lookalike letter prints as the key it
    spoils, and an invisible character not at all, so name it."""
    odd = next((c for c in text if not c.isascii()), None)
    return None if odd is None else f'it holds {_char(odd)}, which is not ASCII: retype it'


def _compile(name: str, duration_ms: int, blocks: tuple[Block, ...]) -> Timeline:
    tracks = []
    for signal in SIGNALS:
        held = DEFAULTS[signal]
        segments = []
        for block in blocks:
            if signal not in block.values:
                continue
            value = block.values[signal]
            if isinstance(value, Ramp):
                segments.append(Segment(block.t_ms, held, value.to, value.ramp_ms))
                held = value.to
            else:
                segments.append(Segment(block.t_ms, held, value))
                held = value
        if segments:
            tracks.append((signal, tuple(segments)))
    return Timeline(name, duration_ms, tuple(tracks))
