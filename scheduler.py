"""
    uv run python scheduler.py serve              # daemon (systemd)
    uv run python scheduler.py run <job> [--no-publish] [--ref SHA]
    uv run python scheduler.py list

Configuration (environment):
    SG_WORK_DIR         work folder (default: ./work next to this file)
    SG_DATA_REMOTE      data repo (default: git@github.com:clarkzjw/starlink-geoip-data.git)
    SG_DATA_BRANCH      branch (default: master)
    SG_GIT_USER_NAME, SG_GIT_USER_EMAIL   commit identity (default: git config)
    GIT_SSH_COMMAND     e.g. "ssh -i ~/.ssh/deploy_key -o IdentitiesOnly=yes"
    SG_LOG_DAYS         keep run logs this many days (default 14)
    SG_KEEP_FAILED_DAYS keep failed run folders this many days (default 7)
"""

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from jobs import JOBS, Job
from publisher import Publisher

CODE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("scheduler")


# -- cron --------------------------------------------------------------------

_FIELDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # day-of-week 0 and 7 = Sunday


def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        has_step = "/" in part
        if has_step:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"bad step in {spec!r}")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = int(part)
            b = hi if has_step else a
        if not (lo <= a <= b <= hi):
            raise ValueError(f"{spec!r} out of range {lo}-{hi}")
        values.update(range(a, b + 1, step))
    return values


class Cron:
    def __init__(self, expr: str):
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"cron needs 5 fields: {expr!r}")
        self.expr = expr
        self.minute, self.hour, self.dom, self.month, self.dow = (
            _parse_field(f, lo, hi) for f, (lo, hi) in zip(fields, _FIELDS)
        )
        if 7 in self.dow:
            self.dow.discard(7)
            self.dow.add(0)
        self.dom_any = fields[2] == "*"
        self.dow_any = fields[4] == "*"

    def matches(self, t: datetime) -> bool:
        if (
            t.minute not in self.minute
            or t.hour not in self.hour
            or t.month not in self.month
        ):
            return False
        dom_ok = t.day in self.dom
        dow_ok = (t.weekday() + 1) % 7 in self.dow  # cron: Sunday = 0
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow_ok
        if self.dow_any:
            return dom_ok
        return dom_ok or dow_ok  # both restricted: cron uses OR

    def next_after(self, t: datetime, limit_days: int = 400) -> datetime | None:
        m = floor_minute(t) + timedelta(minutes=1)
        end = m + timedelta(days=limit_days)
        while m < end:
            if self.matches(m):
                return m
            m += timedelta(minutes=1)
        return None


def floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# -- one run of one job ------------------------------------------------------


class RunFailed(Exception):
    """The job exited non-zero or timed out (already logged)."""


class Runner:
    def __init__(self, work_dir: Path, publisher: Publisher, publish: bool = True):
        self.work_dir = work_dir
        self.publisher = publisher
        self.publish = publish
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self.stopping = threading.Event()

    def run(
        self, job: Job, ref: str | None = None, foreground: bool = False
    ) -> str | None:
        """Snapshot inputs, run the job, publish. Returns the new commit SHA."""
        started = utcnow()
        run_id = started.strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.work_dir / "runs" / job.name / run_id
        input_dir, output_dir, tmp_dir = (
            run_dir / d for d in ("input", "output", "tmp")
        )
        for d in (output_dir, tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        log_path = self.work_dir / "logs" / job.name / f"{run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        self.publisher.snapshot(job.inputs, input_dir)

        cmd = [
            sys.executable,
            "-u",
            str(CODE_DIR / "jobrunner.py"),
            job.name,
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--tmp-dir",
            str(tmp_dir),
            "--now",
            started.isoformat(),
        ]
        if ref:
            cmd += ["--ref", ref]

        log.info(
            "%s: start run %s%s", job.name, run_id, f" ref={ref[:10]}" if ref else ""
        )
        rc = self._exec(job, cmd, log_path, foreground)
        elapsed = utcnow() - started
        if rc != 0:
            log.error(
                "%s: run %s failed (rc=%s) after %s; log %s; files kept in %s",
                job.name,
                run_id,
                rc,
                elapsed,
                log_path,
                run_dir,
            )
            if not foreground:
                log.error("%s: last log lines:\n%s", job.name, _tail(log_path))
            raise RunFailed(f"{job.name} rc={rc}")
        log.info("%s: run %s finished in %s", job.name, run_id, elapsed)

        if not self.publish:
            log.info("%s: not publishing; output left in %s", job.name, output_dir)
            return None
        sha = self.publisher.publish(
            job.name, job.owns, output_dir, f"Scheduled update - {job.name} ({run_id})"
        )
        shutil.rmtree(run_dir, ignore_errors=True)
        return sha

    def _exec(
        self, job: Job, cmd: list[str], log_path: Path, foreground: bool
    ) -> int | str:
        if self.stopping.is_set():
            return "stopping"
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        with open(log_path, "ab") as logf:
            out = None if foreground else logf
            proc = subprocess.Popen(
                cmd,
                cwd=CODE_DIR,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with self._lock:
                self._procs.add(proc)
            try:
                return proc.wait(timeout=job.timeout)
            except subprocess.TimeoutExpired:
                log.error("%s: timed out after %ss; killing", job.name, job.timeout)
                _kill(proc)
                return "timeout"
            finally:
                with self._lock:
                    self._procs.discard(proc)

    def kill_all(self) -> None:
        self.stopping.set()
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            _kill(p)


def _kill(proc: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 20), (signal.SIGKILL, 10)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


# -- scheduling --------------------------------------------------------------


@dataclass
class _State:
    running: bool = False
    pending: bool = False
    pending_ref: str | None = None


class Scheduler:
    def __init__(
        self, jobs: dict[str, Job], execute: Callable[[Job, str | None], str | None]
    ):
        self.jobs = jobs
        self.crons = {n: Cron(j.cron) for n, j in jobs.items() if j.cron}
        self.execute = execute
        self.state = {n: _State() for n in jobs}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="job")

    def request(
        self,
        name: str,
        ref: str | None = None,
        reason: str = "cron",
        queue_if_running: bool = False,
    ) -> None:
        with self.lock:
            if self.stop.is_set():
                return
            st = self.state[name]
            if st.running:
                if queue_if_running:
                    st.pending = True
                    st.pending_ref = ref or st.pending_ref
                    log.info("%s: running; queued one follow-up (%s)", name, reason)
                else:
                    log.info("%s: still running; skipped %s fire", name, reason)
                return
            st.running = True
        log.info("%s: triggered (%s)", name, reason)
        self.pool.submit(self._worker, name, ref)

    def _worker(self, name: str, ref: str | None) -> None:
        job = self.jobs[name]
        while True:
            sha = None
            try:
                sha = self.execute(job, ref)
            except RunFailed:
                pass
            except Exception:
                log.exception("%s: run crashed", name)
            if sha:
                for dep in self.jobs.values():
                    if name in dep.after:
                        self.request(
                            dep.name,
                            ref=sha,
                            reason=f"after {name} {sha[:10]}",
                            queue_if_running=True,
                        )
            with self.lock:
                st = self.state[name]
                if st.pending and not self.stop.is_set():
                    st.pending = False
                    ref, st.pending_ref = st.pending_ref, None
                    continue
                st.running = False
                return

    def due(self, start: datetime, end: datetime) -> list[str]:
        names: list[str] = []
        m = floor_minute(start) + timedelta(minutes=1)
        end = floor_minute(end)
        # After a long stall (suspend), check at most one day of missed minutes.
        m = max(m, end - timedelta(days=1))
        while m <= end:
            for name, cron in self.crons.items():
                if name not in names and cron.matches(m):
                    names.append(name)
            m += timedelta(minutes=1)
        return names

    def serve(self, housekeeping: Callable[[], None] | None = None) -> None:
        last = floor_minute(utcnow())  # don't fire the current minute at startup
        last_housekeeping = 0.0
        while not self.stop.is_set():
            now = utcnow()
            next_minute = last + timedelta(minutes=1)
            if now < next_minute:
                self.stop.wait(min((next_minute - now).total_seconds(), 60))
                continue
            for name in self.due(last, now):
                self.request(name)
            last = floor_minute(now)
            if housekeeping and time.monotonic() - last_housekeeping > 3600:
                last_housekeeping = time.monotonic()
                try:
                    housekeeping()
                except Exception:
                    log.exception("housekeeping failed")

    def shutdown(self) -> None:
        self.stop.set()
        self.pool.shutdown(wait=True, cancel_futures=True)


# -- housekeeping ------------------------------------------------------------


def prune(work_dir: Path) -> None:
    now = time.time()
    log_cutoff = now - int(os.getenv("SG_LOG_DAYS", "14")) * 86400
    run_cutoff = now - int(os.getenv("SG_KEEP_FAILED_DAYS", "7")) * 86400
    for f in (work_dir / "logs").glob("*/*.log"):
        if f.stat().st_mtime < log_cutoff:
            f.unlink(missing_ok=True)
    for d in (work_dir / "runs").glob("*/*"):
        if d.is_dir() and d.stat().st_mtime < run_cutoff:
            shutil.rmtree(d, ignore_errors=True)


# -- CLI ---------------------------------------------------------------------


def make_publisher(work_dir: Path) -> Publisher:
    return Publisher(
        state_dir=work_dir / "state",
        remote=os.getenv(
            "SG_DATA_REMOTE", "git@github.com:clarkzjw/starlink-geoip-data.git"
        ),
        branch=os.getenv("SG_DATA_BRANCH", "master"),
        lock_path=work_dir / "publish.lock",
    )


def cmd_serve(work_dir: Path) -> int:
    publisher = make_publisher(work_dir)
    publisher.ensure_clone()
    runner = Runner(work_dir, publisher)
    sched = Scheduler(JOBS, runner.run)

    def on_signal(signum, _frame):
        log.info(
            "got %s; stopping (running jobs are killed, not published)",
            signal.Signals(signum).name,
        )
        sched.stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("serving %d jobs; work dir %s", len(JOBS), work_dir)
    for name, cron in sched.crons.items():
        log.info("  %-17s %-22s next %s", name, cron.expr, cron.next_after(utcnow()))
    sched.serve(housekeeping=lambda: prune(work_dir))
    runner.kill_all()
    sched.shutdown()
    log.info("stopped")
    return 0


def cmd_run(work_dir: Path, name: str, publish: bool, ref: str | None) -> int:
    publisher = make_publisher(work_dir)
    publisher.ensure_clone()
    runner = Runner(work_dir, publisher, publish=publish)
    try:
        sha = runner.run(JOBS[name], ref=ref, foreground=True)
    except RunFailed:
        return 1
    if publish:
        print(sha or "no commit")
    return 0


def cmd_list() -> int:
    now = utcnow()
    for job in JOBS.values():
        when = (
            f"{job.cron:<22} next {Cron(job.cron).next_after(now):%Y-%m-%d %H:%M}Z"
            if job.cron
            else f"{'after ' + ', '.join(job.after):<22}"
        )
        print(f"{job.name:<17} {when:<46} owns {' '.join(job.owns)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    r = sub.add_parser("run")
    r.add_argument("job", choices=sorted(JOBS))
    r.add_argument("--no-publish", action="store_true")
    r.add_argument("--ref", help="data-repo ref for raw URL reads (map job)")
    sub.add_parser("list")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    work_dir = Path(os.getenv("SG_WORK_DIR", CODE_DIR / "work")).resolve()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "serve":
        return cmd_serve(work_dir)
    return cmd_run(work_dir, args.job, publish=not args.no_publish, ref=args.ref)


if __name__ == "__main__":
    sys.exit(main())
