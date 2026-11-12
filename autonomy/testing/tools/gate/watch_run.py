#!/usr/bin/env python3
"""
End a sim run by sim time, with wall time only as a hang guard.

run_gate.sh and run_faults.sh start this next to each launch. It returns
when the run is over and writes one line to --out saying why: the reason,
the mission state it saw last, the sim seconds since the first /clock and
the wall seconds since it started.

    watch_run.py --cap 180 [--settle 5] [--hang 60] [--pid PID] [--out end.txt]

Reasons:
  finished  the mission reached FINISHED or EMERGENCY and --settle more sim
            seconds passed (only with --settle)
  cap       --cap sim seconds passed since the first /clock
  hung      /clock did not advance for --hang wall seconds (counted from
            start, so a sim that never starts is hung too)
  exited    the launch (--pid) ended on its own

A slow machine only makes a run take longer. Only a sim that stops is cut
short, and that run is marked hung so it can never pass.
"""

import argparse
import sys
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from std_msgs.msg import String

END_STATES = ('FINISHED', 'EMERGENCY')


def alive(pid):
    """Return whether pid is running; an unreaped zombie counts as exited."""
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return False


class Watch(Node):
    """Track the sim clock and the mission state."""

    def __init__(self, settle):
        super().__init__('run_watch')
        self.start = None  # first /clock, sim seconds
        self.sim = None  # latest /clock
        self.moved = time.monotonic()  # wall time /clock last advanced
        self.state = ''
        self.ended = None  # sim time the mission reached an end state
        # Best effort matches any publisher; a dropped /clock sample costs nothing.
        self.create_subscription(Clock, '/clock', self._on_clock, qos_profile_sensor_data)
        if settle is not None:
            self.create_subscription(String, '/lhr/mission/status', self._on_status, 10)

    # Not _clock: rclpy's Node keeps its own clock under that name.
    def _on_clock(self, msg):
        now = msg.clock.sec + msg.clock.nanosec * 1e-9
        if self.start is None:
            self.start = now
        if self.sim is None or now > self.sim:
            self.moved = time.monotonic()
        self.sim = now

    def _on_status(self, msg):
        self.state = msg.data
        if self.ended is None and msg.data in END_STATES:
            self.ended = self.sim if self.sim is not None else 0.0

    def elapsed(self):
        return 0.0 if self.start is None else self.sim - self.start


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--cap', type=float, required=True, help='sim seconds')
    ap.add_argument('--settle', type=float, help='sim seconds after FINISHED or EMERGENCY')
    ap.add_argument('--hang', type=float, default=60.0, help='wall seconds')
    ap.add_argument('--pid', type=int, help='the launch; ends the watch when it exits')
    ap.add_argument('--out', help='file for the result line')
    args = ap.parse_args()

    rclpy.init()
    node = Watch(args.settle)
    started = time.monotonic()
    reason, code = None, 0
    try:
        while reason is None:
            rclpy.spin_once(node, timeout_sec=0.1)
            if args.pid is not None and not alive(args.pid):
                reason = 'exited'
            elif node.ended is not None and (node.sim or 0.0) - node.ended >= args.settle:
                reason = 'finished'
            elif node.start is not None and node.elapsed() >= args.cap:
                reason = 'cap'
            elif time.monotonic() - node.moved >= args.hang:
                reason = 'hung'
    except (KeyboardInterrupt, ExternalShutdownException):
        # Ctrl-C, or the runner stopping the run with TERM.
        reason, code = 'interrupted', 130
    line = (f'reason={reason} mission={node.state or "-"} '
            f'sim_s={node.elapsed():.1f} wall_s={time.monotonic() - started:.1f}')
    print(line, flush=True)
    if args.out:
        with open(args.out, 'w') as f:
            f.write(line + '\n')
    rclpy.try_shutdown()
    return code


if __name__ == '__main__':
    sys.exit(main())
