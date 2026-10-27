#!/usr/bin/env python3
"""IoT event agent: parse -> policy -> action.

This is deliberately split from iot_event.py and iot_status.py:

    iot_mailbox.py  low-level /dev/mem mailbox
    iot_status.py   status ack (WIN_READY / SHUTING / SUSPENDING)
    iot_event.py    event frame parser (shutdown/sleep/wake/status)
    iot_agent.py    policy and execution (systemctl poweroff/suspend)

By default the agent is a dry run.  Real shutdown/sleep needs both
--execute and the matching --allow-* flag.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass

from iot_event import (Event, FileEventSource, InputEventSource,
                       JournalEventSource, MailboxEventSource, simulate)
from iot_mailbox import Mailbox
from iot_status import SHUTING, SUSPENDING, WIN_READY, send_status


@dataclass
class Policy:
    dry_run: bool = True
    allow_shutdown: bool = False
    allow_sleep: bool = False
    quiet: bool = False

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)


class Runner:
    def __init__(self, policy: Policy):
        self.policy = policy

    def _status(self, status: int) -> None:
        try:
            with Mailbox() as mbox:
                send_status(status, bus=mbox)
            self.policy.log(f"  ack -> 0x{status:02x}")
        except Exception as e:  # noqa: BLE001
            self.policy.log(f"  ack -> 0x{status:02x} failed: {e}")

    def _run(self, argv: list[str]) -> None:
        self.policy.log(f"  running: {' '.join(argv)}")
        subprocess.run(argv, check=False)

    def shutdown(self) -> None:
        self.policy.log("shutdown event")
        if not self.policy.allow_shutdown:
            self.policy.log("  shutdown not allowed (need --allow-shutdown)")
            return
        if self.policy.dry_run:
            self.policy.log("  dry-run: would run systemctl poweroff")
            return
        self._status(SHUTING)
        self._run(["systemctl", "poweroff"])

    def sleep(self) -> None:
        self.policy.log("sleep event")
        if not self.policy.allow_sleep:
            self.policy.log("  sleep not allowed (need --allow-sleep)")
            return
        if self.policy.dry_run:
            self.policy.log("  dry-run: would run systemctl suspend")
            return
        self._status(SUSPENDING)
        self._run(["systemctl", "suspend"])

    def wake(self) -> None:
        self.policy.log("wake event")
        self._status(WIN_READY)

    def status(self) -> None:
        self.policy.log("status event")
        self._status(WIN_READY)

    def dispatch(self, event: Event) -> None:
        if event.kind == "shutdown":
            self.shutdown()
        elif event.kind == "sleep":
            self.sleep()
        elif event.kind == "wake":
            self.wake()
        elif event.kind == "status":
            self.status()
        else:
            self.policy.log(f"ignore: {event}")


def run_source(source, runner: Runner, once: bool = False) -> None:
    for event in source:
        runner.dispatch(event)
        if once:
            return


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["input", "mailbox", "file", "journal"],
                    default="input")
    ap.add_argument("--file", help="event file for --source file")
    ap.add_argument("--since", help="journalctl --since for --source journal")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--execute", action="store_true",
                    help="actually run systemctl (default: dry-run)")
    ap.add_argument("--allow-shutdown", action="store_true")
    ap.add_argument("--allow-sleep", action="store_true")
    ap.add_argument("--simulate", choices=["shutdown", "sleep", "wake"],
                    help="dispatch one synthetic event and exit")
    ap.add_argument("--dump", action="store_true",
                    help="print raw mailbox changes")
    ap.add_argument("--once", action="store_true",
                    help="stop after the first event")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    policy = Policy(
        dry_run=not args.execute,
        allow_shutdown=args.allow_shutdown,
        allow_sleep=args.allow_sleep,
        quiet=args.quiet,
    )
    runner = Runner(policy)

    if args.simulate:
        runner.dispatch(simulate(args.simulate))
        return

    if args.source == "file":
        if not args.file:
            ap.error("--source file needs --file")
        run_source(FileEventSource(args.file), runner, args.once)
        return

    if args.source == "journal":
        run_source(JournalEventSource(since=args.since or "now"), runner, args.once)
        return

    if args.source == "input":
        run_source(InputEventSource(), runner, args.once)
        return

    with MailboxEventSource(dump=args.dump) as source:
        print(f"watching IoT mailbox every {args.interval}s "
              f"({'dry-run' if policy.dry_run else 'execute'})", flush=True)
        try:
            while True:
                for event in source.poll():
                    print(f"event: {event}", flush=True)
                    runner.dispatch(event)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    sys.exit(main())
