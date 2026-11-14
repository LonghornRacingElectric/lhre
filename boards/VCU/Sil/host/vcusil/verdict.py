"""A run's verdict: the invariants and the file's expectations, together,
and how a run reports them (meta.json, run.mcap's /events, summary.txt and
stderr). A failed check makes python -m vcusil exit 3.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import events, expectations, invariants
from .cosim import STEP_MS
from .expectations import BOOT_ACCEPTED, SHUTDOWN_OPEN_BIT
from .fwmodel import LOOP_MS
from .scenario_file import ScenarioFile


@dataclass(frozen=True)
class Verdict:
    invariants: tuple[invariants.Result, ...]
    expectations: tuple[expectations.Result, ...]
    accepted: tuple[str, ...]  # known non-failures the checks let through

    @property
    def failed(self) -> bool:
        return (any(r.failed for r in self.invariants)
                or any(r.failed for r in self.expectations))

    @property
    def status(self) -> str:
        return 'failed' if self.failed else 'passed'

    def meta(self) -> dict[str, object]:
        def count(results, verdict):
            return sum(r.result == verdict for r in results)
        return {
            'status': self.status,
            'invariants': [r.meta() for r in self.invariants],
            'expectations': [r.meta() for r in self.expectations],
            'counts': {
                'invariants_failed': count(self.invariants, 'fail'),
                'invariants_waived': count(self.invariants, 'waived'),
                'unused_waivers': count(self.invariants, 'unused_waiver'),
                'expectations_passed': count(self.expectations, 'pass'),
                'expectations_failed': count(self.expectations, 'fail'),
                'expectations_not_evaluated': count(self.expectations, 'not_evaluated'),
            },
            'accepted': list(self.accepted),
        }

    def headline(self) -> str:
        c = self.meta()['counts']
        passed = len(self.invariants) - c['invariants_failed'] - c['invariants_waived'] - (
            c['unused_waivers'])
        parts = [f'{passed}/{len(self.invariants)} invariants passed']
        if c['invariants_waived']:
            parts.append(f'{c["invariants_waived"]} waived')
        if c['unused_waivers']:
            parts.append(f'{c["unused_waivers"]} unused waiver'
                         + ('s' if c['unused_waivers'] != 1 else ''))
        if self.expectations:
            parts.append(f'{c["expectations_passed"]}/{len(self.expectations)} expectations '
                         'passed')
            if c['expectations_not_evaluated']:
                parts.append(f'{c["expectations_not_evaluated"]} not evaluated')
        return f'checks {self.status}: ' + ', '.join(parts)

    def lines(self, sf: ScenarioFile) -> list[str]:
        """One line per check that did not simply pass, led by file:line: as
        check's are, so an editor's terminal opens it."""
        def place(line: int | None) -> str:
            return f'{sf.display}:{line}' if line else sf.display
        out = []
        for r in self.invariants:
            if r.result == 'fail':
                out.append(f'{place(None)}: FAIL: {r.message} ({r.rule}) [{r.name}]')
            elif r.result == 'waived':
                out.append(f'{place(sf.lines.key("waive", r.name))}: waived: {r.message}; the '
                           f'file says: {r.reason} [{r.name}]')
            elif r.result == 'unused_waiver':
                out.append(f'{place(sf.lines.key("waive", r.name))}: FAIL: waive.{r.name}: '
                           f'{r.message} [unused_waiver]')
        for r in self.expectations:
            if r.result == 'fail':
                out.append(f'{place(r.line)}: FAIL: expect[{r.index}] {r.text}: {r.measured} '
                           '[expect_failed]')
            elif r.result == 'not_evaluated':
                out.append(f'{place(r.line)}: not evaluated: expect[{r.index}] {r.text}: '
                           f'{r.measured} [not_evaluated]')
        return out

    def events(self, end_ms: int) -> list[events.Event]:
        """/events markers: each failure at its first tick, each passed
        expectation at the tick it rests on, and the verdict at the end."""
        found = []
        for r in self.invariants:
            if r.result in ('fail', 'waived'):
                level = events.ERROR if r.result == 'fail' else events.WARNING
                verb = 'broken' if r.result == 'fail' else 'broken, waived'
                found.append(events.Event(r.first_ms, level, 'check.invariant',
                                          f'{r.name} {verb}: {r.message}'))
            elif r.result == 'unused_waiver':
                found.append(events.Event(end_ms, events.ERROR, 'check.invariant',
                                          f'{r.name}: {r.message}'))
        for r in self.expectations:
            at = r.at_ms if r.at_ms is not None else end_ms
            level = {'pass': events.INFO, 'fail': events.ERROR}.get(r.result, events.WARNING)
            found.append(events.Event(at, level, 'check.expect',
                                      f'expect[{r.index}] {r.result}: {r.text}: {r.measured}'))
        found.append(events.Event(end_ms, events.ERROR if self.failed else events.INFO,
                                  'check', self.headline()))
        return found


def judge(sf: ScenarioFile, rows: list[dict[str, float | int]]) -> Verdict:
    accepted = []
    fault_checks = any(e.condition is not None and e.condition.kind == 'fault'
                       for e in sf.expectations)
    if fault_checks and any(int(r['t_ms']) < LOOP_MS and int(r['fw_faults']) & SHUTDOWN_OPEN_BIT
                            for r in rows):
        accepted.append(BOOT_ACCEPTED)
    complete = len(rows) == -(-sf.duration_ms // STEP_MS)
    return Verdict(invariants.check(rows, sf.waive, complete), expectations.evaluate(sf, rows),
                   tuple(accepted))


def with_events(marks: list[events.Event], verdict: Verdict) -> list[events.Event]:
    """The run's events with the verdict's, in time order; the run's last
    event (its end) stays last."""
    if not marks:
        return marks
    end = marks[-1]
    return sorted(marks[:-1] + verdict.events(end.t_ms), key=lambda e: e.t_ms) + [end]
