"""Scenario files: boards/VCU/Sil/scenarios/<name>.toml, checked and compiled
into a scenario.Timeline.

The format, lhre.vcu-sil-scenario.v1, is in boards/VCU/Sil/README.md
(section Scenarios). Every problem in a file is reported at once, with its
line, before anything builds; check.py adds the warnings. Standard library
only, so the check runs anywhere Python 3.11 or newer does.
"""

from __future__ import annotations

import copy
import datetime
import difflib
import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

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
# The warnings check.py can raise, which [allow] may name (check.RULES).
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
    """Run conditions from [setup]; None where the file leaves them out."""
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
    """Best-effort lines of keys: tomllib gives no positions for a valid
    document. '' is a table's or block's header line."""
    top: dict[str, int] = field(default_factory=dict)
    tables: dict[str, dict[str, int]] = field(default_factory=dict)
    blocks: list[dict[str, int]] = field(default_factory=list)

    def table(self, name: str, key: str | None = None) -> int | None:
        lines = self.tables.get(name)
        if lines is None:  # written inline, setup = { ... }: one line for all
            return self.top.get(name)
        return lines.get(key, lines['']) if key else lines['']

    def block(self, index: int, key: str | None = None) -> int | None:
        if index >= len(self.blocks):  # an inline array of tables has no headers
            return self.top.get('at')
        lines = self.blocks[index]
        return lines.get(key, lines['']) if key else lines['']


# A key part, bare or quoted: TOML reads [["at"]] as [[at]].
_PART = r'(?:[A-Za-z0-9_-]+|"[^"\n]*"|\'[^\'\n]*\')'
_HEADER = re.compile(rf'\s*(\[\[?)\s*({_PART}(?:\s*\.\s*{_PART})*)\s*\]')
_KEY = re.compile(rf'\s*({_PART})\s*=')


def _lines(text: str) -> list[str]:
    """The lines as tomllib counts them: str.splitlines() also breaks at
    U+2028, U+0085 and others, which TOML allows in comments and strings."""
    return [line.rstrip('\r') for line in text.split('\n')]


def _locate(text: str) -> _Lines:
    found = _Lines()
    current = found.top
    for n, line in enumerate(_lines(text), 1):
        header = _HEADER.match(line)
        if header:
            name = '.'.join(part.strip('"\'') for part in re.findall(_PART, header[2]))
            if header[1] == '[[' and name == 'at':
                current = {'': n}
                found.blocks.append(current)
            else:
                current = found.tables.setdefault(name, {'': n})
            continue
        key = _KEY.match(line)
        if key:
            current.setdefault(key[1].strip('"\''), n)
    return found


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
    dot-files, such as macOS's ._x.toml and Emacs's .#x.toml lock links,
    which are not scenarios."""
    return sorted((p for p in SCENARIO_DIR.glob('*.toml')
                   if p.is_file() and not p.name.startswith('.')), key=lambda p: p.stem)


def names() -> list[str]:
    return [p.stem for p in scenario_paths()]


def _slug(text: str, fallback: str = 'my-scenario') -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or fallback


def _name_on_disk(path: Path) -> str:
    """path's file name as its directory lists it. A case-insensitive
    filesystem (macOS) finds LAUNCH.toml when the file is launch.toml."""
    try:
        entries = os.listdir(path.parent)
    except OSError:
        return path.name
    if path.name in entries:
        return path.name
    return next((e for e in entries if e.lower() == path.name.lower()), path.name)


def resolve(arg: str) -> Path:
    """A name from scenarios/, or a path to a .toml file (one with a / or
    ending in .toml). Raises ScenarioError (not_found, bad_name)."""
    if '/' in arg or arg.endswith('.toml'):
        path = Path(arg)
        if not path.is_file():
            # The usual slip: launch.toml, or scenarios/launch, from another
            # directory, when the name alone would do.
            known = names()
            stem = path.stem.lower()
            close = [stem] if stem in known else difflib.get_close_matches(stem, known, n=1)
            hint = (f'; did you mean {close[0]}? scenarios/ has: {", ".join(known)}.' if close
                    else '')
            raise ScenarioError([Problem('error', 'not_found', '',
                                         f'no scenario file at {arg}{hint}')])
        on_disk = Path(_name_on_disk(path))
        if on_disk.suffix != '.toml' or not NAME.fullmatch(on_disk.stem):
            which = 'it' if on_disk.name == path.name else on_disk.name
            raise ScenarioError([Problem(
                'error', 'bad_name', '',
                'a scenario file is named in lowercase words joined by dashes and ends in '
                f'.toml; rename {which} to {_slug(on_disk.stem)}.toml')], arg)
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
            hint = f'; a scenario file ends in .toml: rename it to {_slug(Path(arg).stem)}.toml'
        elif _slug(arg) in names():
            hint = f'; did you mean {_slug(arg)}?'
        else:
            hint = ''
        raise ScenarioError([Problem(
            'error', 'bad_name', '',
            f'{arg!r} is not a scenario name: names are lowercase words joined by dashes, like '
            f'my-launch{hint}')])
    path = SCENARIO_DIR / f'{arg}.toml'
    if not path.is_file():
        known = names()
        close = difflib.get_close_matches(arg, known, n=1)
        raise ScenarioError([Problem(
            'error', 'not_found', '',
            f'no scenario named {arg!r}' + (f'; did you mean {close[0]}?' if close else '.')
            + f' scenarios/ has: {", ".join(known) or "nothing"}. A path to a .toml file also '
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
    display = repo_path or (str(path) if path is not None else f'{name}.toml')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        line = data[:exc.start].count(b'\n') + 1
        raise ScenarioError([Problem('error', 'toml_syntax', '',
                                     f'byte {exc.start} is not UTF-8 text; save the file as '
                                     'UTF-8', line)], display) from exc
    if text.startswith('\ufeff'):
        # Refused rather than skipped, so a file has one form, as eol=lf in
        # .gitattributes gives it one line ending. tomllib says only
        # 'Invalid statement' at line 1.
        raise ScenarioError([Problem('error', 'toml_syntax', '',
                                     'the file starts with a byte-order mark (some Windows '
                                     'editors add one); save it as UTF-8 without a BOM', 1)],
                            display)
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ScenarioError([_syntax_problem(str(exc), text)], display) from exc
    except (ValueError, RecursionError) as exc:
        # tomllib lets two through: int()'s 4300-digit limit, whose advice
        # after the ; is for Python code, and nesting too deep to recurse.
        reason = ('arrays or inline tables are nested too deeply' if isinstance(
            exc, RecursionError) else str(exc).split(';')[0])
        raise ScenarioError([_syntax_problem(reason, text)], display) from exc
    lines = _locate(text)
    checker = _Checker(lines)
    checked = checker.check(doc)
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
        document=document, lines=lines)


_POSITION = re.compile(r'\s*\(at line (\d+), column (\d+)\)$')


def _syntax_problem(message: str, text: str) -> Problem:
    """tomllib's message and position, with a hint for the usual slips."""
    position = _POSITION.search(message)
    line = column = None
    if position:
        line, column = int(position[1]), int(position[2])
        message = message[:position.start()]
    elif message.endswith(' (at end of document)'):
        message = message[:-len(' (at end of document)')]
        line = text.count('\n') + 1
    hint = _syntax_hint(message, text, line, column)
    return Problem('error', 'toml_syntax', '',
                   f'TOML: {message}' + (f' at column {column}' if column else '')
                   + (f'; {hint}' if hint else ''), line)


# What a bare word should have been, by the key it is written for (a ramp's
# to: its signal's). Names, text and unknown keys get the quoted-name hint.
_KINDS = {**{key: signal.kind for key, signal in SIGNALS.items()}, 'soc_start': 'number',
          'event_mode': 'name', 't_ms': 'ms', 'duration_ms': 'ms', 'ramp_ms': 'ms'}
_KEY_BEFORE = re.compile(rf'({_PART})\s*=\s*$')
_ONE_LINE = 'an inline table { ... } must fit on one line, with no comma before }'


def _syntax_hint(message: str, text: str, line: int | None, column: int | None) -> str | None:
    rows = _lines(text)
    row = rows[line - 1] if line and line <= len(rows) else ''
    rest = row[column - 1:] if column and column <= len(row) else ''
    word = re.match(r'[+-]?([^\W\d_]\w*)', rest)  # -Inf is reported at the -
    if message == 'Invalid value' and word:
        return _word_hint(word[1], row, column)
    if message == 'Invalid value' and re.match(r'[+-]?\.', rest):
        return 'write a leading zero: 0.5'
    if message == 'Unclosed inline table' and '{' in row:
        # tomllib says this at anything after a value other than , or }.
        if rest.strip()[:1] in ('', '#'):
            return _ONE_LINE
        hint = 'separate the keys with a comma: { to = 0.4, ramp_ms = 300 }'
        if rest[:1].isalpha() and row[column - 2:column - 1].isdigit():  # ramp_ms = 300ms
            return f'units go in a # comment; {hint}'
        return hint
    if message == 'Invalid initial character for a key part' and '{' in row:
        return _ONE_LINE
    if message == 'Cannot overwrite a value':
        return 'a key or table is set twice; each moment needs its own [[at]] block'
    if message.startswith('Cannot declare') and message.endswith('twice'):
        return 'this table is written twice; merge them'
    return None


def _word_hint(word: str, row: str, column: int) -> str:
    """For a bare word where a value goes: the fix that the key it is written
    for takes, so following the hint never leads to a second error."""
    key = _KEY_BEFORE.search(row[:column - 1])
    key = key[1].strip('"\'') if key else None
    if key == 'to':
        outer = _KEY.match(row)
        key = outer[1].strip('"\'') if outer else None
    kind = _KINDS.get(key)
    if kind == 'number':
        if word.lower() in ('nan', 'inf'):
            return 'nan and inf are lowercase in TOML, and out of range here anyway'
        return 'a number is written bare, such as 0.5'
    if kind == 'ms':
        return 'milliseconds are a whole number written bare, such as 1500'
    if kind != 'name' and word.lower() in ('true', 'false'):
        return 'booleans are lowercase: true or false'
    if kind == 'bool':
        return 'write true or false'
    return 'a name is a string in quotes: hvc_state = "ENERGIZED"'


def _toml(value: object) -> str:
    """A value as TOML spells it, for messages."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict):
        return '{ ' + ', '.join(f'{k} = {_toml(v)}' for k, v in value.items()) + ' }'
    if isinstance(value, list):
        return '[' + ', '.join(_toml(v) for v in value) + ']'
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


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
            self.add('wrong_schema', 'schema', f'{_toml(schema)}: this file was written for a '
                     f'different harness version; this one reads {SCHEMA}',
                     self.lines.top.get('schema'))
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
                self.add('wrong_type', 'schema', f'{_toml(schema)} is not a string; write '
                         f'schema = "{SCHEMA}"', self.lines.top.get('schema'))
            elif schema != SCHEMA:
                self.add('wrong_schema', 'schema', f'{_toml(schema)} is not "{SCHEMA}"',
                         self.lines.top.get('schema'))
        document['schema'] = SCHEMA
        description = self._text(doc, 'description', 'description', self.lines.top.get(
            'description'), 'say in one line what the drive does', MAX_DESCRIPTION)
        document['description'] = description
        duration = None
        if 'duration_ms' in doc:
            line = self.lines.top.get('duration_ms')
            duration = self._ms(doc['duration_ms'], 'duration_ms', line)
            if duration is not None and not 1 <= duration <= MAX_DURATION_MS:
                self.add('out_of_range', 'duration_ms', f'{duration} is outside '
                         f'1..{MAX_DURATION_MS} ms (10 minutes)', line)
                duration = None
        document['duration_ms'] = duration
        setup, document['setup'] = self._setup(doc.get('setup', {}))
        allow = self._allow(doc.get('allow', {}))
        document['allow'] = dict(allow)
        blocks, document['at'] = self._blocks(doc.get('at'), duration)
        return description, duration, setup, allow, blocks, document

    def _unknown_top(self, key: str) -> None:
        if key in PLANNED:
            hint = PLANNED[key]
        elif key in SIGNALS:
            hint = 'signals go in an [[at]] block, at the time they change'
        elif key in SETUP_KEYS:
            hint = f'{key} goes in [setup]'
        else:
            hint = _did_you_mean(key, TOP_KEYS)
        self.add('unknown_key', key, _hinted(f'is not an {SCHEMA} key', hint),
                 self.lines.top.get(key) or self.lines.table(key))

    def _missing_top(self, key: str) -> None:
        # TOML files a key written after a [table] under that table.
        inside = [name for name, lines in self.lines.tables.items() if key in lines]
        inside += [f'at[{i}]' for i, lines in enumerate(self.lines.blocks) if key in lines]
        hint = ''
        if key == 'at':
            hint = '; a scenario needs at least one [[at]] block'
        elif inside:
            table = inside[0] if inside[0].startswith('at[') else f'[{inside[0]}]'
            hint = (f'; the {key} written in {table} counts as part of it: top-level keys go '
                    'before the first [table]')
        self.add('missing_key', key, 'is missing' + hint, None)

    def _text(self, table: dict, key: str, where: str, line: int | None, what: str,
              limit: int | None = None) -> str | None:
        if key not in table:
            return None
        value = table[key]
        if not isinstance(value, str):
            self.add('wrong_type', where, f'{_toml(value)} is not a string; write it in quotes',
                     line)
        elif not value.strip():
            self.add('bad_value', where, f'is empty; {what}', line)
        elif value.splitlines() != [value]:  # any break str knows, U+2028 too
            self.add('bad_value', where, 'is more than one line; keep it to one and put the '
                     'rest in # comments', line)
        elif limit is not None and len(value) > limit:
            self.add('bad_value', where, f'is {len(value)} characters; the limit is {limit} '
                     '(put the rest in # comments)', line)
        else:
            return value
        return None

    def _ms(self, value: object, where: str, line: int | None) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            self.add('wrong_type', where, f'{_toml(value)} is not whole milliseconds; write an '
                     'integer, such as 1500', line)
            return None
        return value

    def _number(self, value: object, where: str, line: int | None, lo: float, hi: float,
                unit: str) -> float | None:
        # A bool is an int to Python, never a number in a scenario.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.add('wrong_type', where, f'{_toml(value)} is not a number ({unit})', line)
            return None
        if not lo <= value <= hi:  # also false for nan
            self.add('out_of_range', where, f'{_toml(value)} is outside {_range(lo, hi)} '
                     f'({unit})', line)
            return None
        return float(value) + 0.0  # an integer becomes a float, -0.0 becomes 0.0

    def _name(self, value: object, key: str, table: dict[str, int], kind: str, where: str,
              line: int | None) -> int | None:
        if not isinstance(value, str):
            named = {v: k for k, v in table.items()}
            example = named.get(value) if type(value) is int else None  # not for a bool
            hint = (f'write the name: {key} = "{example}"' if example
                    else f'write the name in quotes, one of {", ".join(table)}')
            self.add('wrong_type', where, f'{_toml(value)} is not an lhre {kind} name; {hint}',
                     line)
            return None
        if value in table:
            return table[value]
        upper = {name.lower(): name for name in table}
        if value.lower() in upper:
            hint = f'did you mean {upper[value.lower()]}? names are upper case'
        else:
            hint = _did_you_mean(value.upper(), table) or f'one of {", ".join(table)}'
        self.add('bad_value', where, f'{_toml(value)} is not an lhre {kind} name; {hint}', line)
        return None

    def _setup(self, setup: object) -> tuple[Setup, dict]:
        line = self.lines.table('setup') or self.lines.top.get('setup')
        if not isinstance(setup, dict):
            self.add('wrong_type', 'setup', f'{_toml(setup)} is not a table; write [setup] '
                     'on its own line, then its keys', line)
            return Setup(), {}
        written: dict = {}
        event_mode = soc_start = None
        for key, value in setup.items():
            where, key_line = f'setup.{key}', self.lines.table('setup', key)
            if key == 'event_mode':
                event_mode = self._name(value, key, EVENT_MODES, 'EventMode', where, key_line)
                if event_mode is not None:
                    written[key] = value
            elif key == 'soc_start':
                soc_start = self._number(value, where, key_line, 0.0, 1.0,
                                         'the state of charge as a fraction: 0.9 is 90 %')
                if soc_start is not None:
                    written[key] = soc_start
            else:
                if key in TOP_KEYS:
                    hint = _AFTER_TABLE.format(table='[setup]')
                elif key == 'brake_psi_full':
                    hint = ('it stays the --brake-psi-full option: it calibrates the car model, '
                            'not the drive')
                else:
                    hint = _did_you_mean(key, SETUP_KEYS)
                self.add('unknown_key', where, _hinted('is not a [setup] key', hint), key_line)
        return Setup(event_mode, soc_start), written

    def _allow(self, allow: object) -> dict[str, str]:
        if not isinstance(allow, dict):
            self.add('wrong_type', 'allow', f'{_toml(allow)} is not a table; write [allow] on '
                     'its own line, then code = "reason" lines',
                     self.lines.table('allow') or self.lines.top.get('allow'))
            return {}
        allowed = {}
        for code in allow:
            where, line = f'allow.{code}', self.lines.table('allow', code)
            if code == 'unused_allow':
                self.add('unknown_key', where, 'cannot be allowed: it is the warning for an '
                         '[allow] entry that never fires', line)
                continue
            if code not in WARNINGS:
                hint = (_AFTER_TABLE.format(table='[allow]') if code in TOP_KEYS
                        else _did_you_mean(code, WARNINGS))
                self.add('unknown_key', where, _hinted('is not a warning code', hint), line)
                continue
            text = self._text(allow, code, where, line, 'say why the drive sets it off')
            if text is not None:
                allowed[code] = text
        return allowed

    def _blocks(self, at: object, duration: int | None) -> tuple[tuple[Block, ...], list]:
        if at is None:
            return (), []
        if isinstance(at, dict):
            self.add('wrong_type', 'at', 'is a single table; write [[at]], with two brackets, '
                     'once per moment', self.lines.table('at'))
            return (), []
        if not isinstance(at, list) or not all(isinstance(b, dict) for b in at):
            self.add('wrong_type', 'at', f'{_toml(at)} is not a list of blocks; write [[at]], '
                     'with two brackets, once per moment', self.lines.top.get('at'))
            return (), []
        if not at:
            self.add('missing_key', 'at', 'has no blocks; a scenario needs at least one [[at]]',
                     self.lines.top.get('at'))
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
                compiled = self._signal(key, value, key_where, line)
                if compiled is None:
                    continue
                values[key] = compiled
                if isinstance(compiled, Ramp):
                    entry[key] = {'to': compiled.to, 'ramp_ms': compiled.ramp_ms}
                else:  # a name as written, a number as a float
                    entry[key] = value if key == 'hvc_state' else compiled
            else:
                self._unknown_in_block(key, key_where, line, where)
        if set(block) <= {'t_ms'}:
            self.add('sets_nothing', where, 'sets nothing; give it a signal or a mark, or remove '
                     'it', self.lines.block(i))
        return t, entry, values, mark

    def _mark(self, value: object, where: str, line: int | None, i: int,
              marks: dict[str, int]) -> str | None:
        if not isinstance(value, str):
            self.add('wrong_type', where, f'{_toml(value)} is not a string; a mark is a name in '
                     'quotes, such as "loop-open"', line)
            return None
        if not NAME.fullmatch(value) or len(value) > MAX_MARK:
            # The cut can end on a dash, which no mark may, and a mark of
            # no letters or digits has no slug at all.
            example = _slug(value, '')[:MAX_MARK].rstrip('-') or 'loop-open'
            self.add('bad_value', where, f'{_toml(value)} is not a mark name: at most '
                     f'{MAX_MARK} characters of lowercase words joined by dashes, such as '
                     f'"{example}"', line)
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
            hint = _AFTER_TABLE.format(table=block)
        elif key in SETUP_KEYS:
            hint = f'{key} goes in [setup]'
        else:
            hint = _did_you_mean(key, BLOCK_KEYS)
        self.add('unknown_key', where, _hinted(f'is not an {SCHEMA} key', hint), line)

    def _signal(self, key: str, value: object, where: str, line: int | None) -> object | None:
        signal = SIGNALS[key]
        if isinstance(value, dict):
            return self._ramp(key, signal, value, where, line)
        if signal.kind == 'number':
            return self._number(value, where, line, signal.lo, signal.hi, signal.unit)
        if signal.kind == 'bool':
            if not isinstance(value, bool):
                self.add('wrong_type', where, f'{_toml(value)} is not true or false; write true '
                         'or false', line)
                return None
            return value
        return self._name(value, key, HVC_STATES, 'HvcState', where, line)

    def _ramp(self, key: str, signal: Signal, ramp: dict, where: str,
              line: int | None) -> Ramp | None:
        if signal.kind == 'bool':
            self.add('bad_ramp', where, 'is a switch and cannot ramp; write true or false', line)
            return None
        if signal.kind != 'number':
            self.add('bad_ramp', where, 'is a state and cannot ramp; write its name, such as '
                     '"ENERGIZED"', line)
            return None
        extra = [k for k in ramp if k not in ('to', 'ramp_ms')]
        missing = [k for k in ('to', 'ramp_ms') if k not in ramp]
        if extra or missing:
            what = '; '.join([f'{k} is not a ramp key' for k in extra]
                             + [f'it has no {k}' for k in missing])
            self.add('bad_ramp', where, f'a ramp is {{ to = X, ramp_ms = N }}: {what}', line)
            return None
        to = self._number(ramp['to'], f'{where}.to', line, signal.lo, signal.hi, signal.unit)
        ramp_ms = self._ms(ramp['ramp_ms'], f'{where}.ramp_ms', line)
        if ramp_ms is not None and ramp_ms < 1:
            self.add('bad_ramp', f'{where}.ramp_ms', f'{ramp_ms} is under 1 ms; a ramp takes at '
                     'least 1 ms (write a plain value for a step)', line)
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


_AFTER_TABLE = ('top-level keys go before the first [table], so TOML put this one in '
                '{table}')


def _hinted(message: str, hint: str | None) -> str:
    return f'{message}; {hint}' if hint else message


def _did_you_mean(key: str, choices) -> str | None:
    close = difflib.get_close_matches(key, list(choices), n=1)
    return f'did you mean {close[0]}?' if close else None


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
