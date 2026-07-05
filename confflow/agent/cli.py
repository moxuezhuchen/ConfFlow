"""ConfFlow Agent CLI: serve / status / submit / list / pause / resume / cancel / stop."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from .queue import JobQueue, JobSpec
from .server import AgentServer
from .slots import SlotManager
from .state import AgentStateDB, JobStatus, CLEAR

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _state_db_from_args(args: argparse.Namespace) -> AgentStateDB:
    return AgentStateDB(args.state_db)


def _queue_dir_from_args(args: argparse.Namespace) -> str:
    return args.queue_dir


# ---------------------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    db = _state_db_from_args(args)
    server = AgentServer(
        queue_dir=args.queue_dir,
        state_db=db,
        num_slots=args.slots,
    )
    print(f"ConfFlow Agent serving on queue={args.queue_dir} slots={args.slots}")
    print("Press Ctrl+C or send SIGTERM to stop.")
    try:
        server.serve()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()
    return 0


# ---------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    db = _state_db_from_args(args)
    job = db.get_job(args.job_id)
    if job is None:
        print(f"Job {args.job_id} not found.", file=sys.stderr)
        return 1

    status_dir = Path(args.queue_dir) / "status"
    status_file = status_dir / f"{args.job_id}.json"
    extra: dict = {}
    if status_file.exists():
        try:
            extra = json.loads(status_file.read_text()).get("event", {})
        except (OSError, json.JSONDecodeError):
            pass

    print(textwrap.dedent(f"""\
        Job ID:         {job['job_id']}
        Status:         {job['status']}
        Config:         {job['config_file']}
        Input:          {job['input_xyz']}
        Work Dir:       {job['work_dir'] or '(not started)'}
        Progress:       {job['progress_pct']:.0f}%
        Current Step:   {job['current_step'] or 'N/A'}
        Submitted At:   {job['submitted_at']}
        Started At:     {job['started_at'] or 'N/A'}
        Completed At:   {job['completed_at'] or 'N/A'}
        Error:          {job['error_message'] or 'None'}
    """))
    if extra:
        print("Latest event:", json.dumps(extra, indent=2))
    return 0


# ---------------------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    db = _state_db_from_args(args)
    jobs = db.list_jobs(status=None if not args.no_all else JobStatus.PENDING)
    if not jobs:
        print("No jobs found.")
        return 0

    print(f"{'Job ID':<32} {'Status':<12} {'Progress':<10} {'Work Dir'}")
    print("-" * 90)
    for j in jobs:
        print(f"{j['job_id']:<32} {j['status']:<12} {j['progress_pct']:>6.0f}%   {j['work_dir'] or ''}")
    return 0


# ---------------------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------------------

def cmd_submit(args: argparse.Namespace) -> int:
    import uuid
    _setup_logging(args.verbose)

    queue = JobQueue(args.queue_dir)
    job_id = args.job_id or f"job_{uuid.uuid4().hex[:12]}"

    # Resolve input paths to absolute
    config_file = Path(args.config).resolve()
    input_xyz = Path(args.input_xyz).resolve()

    if not config_file.exists():
        print(f"Config file not found: {config_file}", file=sys.stderr)
        return 1
    if not input_xyz.exists():
        print(f"Input XYZ not found: {input_xyz}", file=sys.stderr)
        return 1

    spec = JobSpec(
        job_id=job_id,
        config_file=str(config_file),
        input_xyz=str(input_xyz),
        submitted_at=datetime.utcnow().isoformat() + "Z",
        submitted_by=args.submitted_by or "cli",
    )
    queue.enqueue(spec)

    # Also register in state DB so status is queryable before agent picks it up
    state_db = AgentStateDB(args.state_db)
    state_db.add_job(
        job_id=job_id,
        config_file=str(config_file),
        input_xyz=str(input_xyz),
        submitted_at=spec.submitted_at,
        submitted_by=spec.submitted_by,
    )
    state_db.set_status(job_id, JobStatus.PENDING)
    state_db.close()

    print(f"Job {job_id} submitted to queue.")
    return 0


# ---------------------------------------------------------------------------------------
# pause / resume / cancel
# ---------------------------------------------------------------------------------------

def cmd_pause(args: argparse.Namespace) -> int:
    db = _state_db_from_args(args)
    job = db.get_job(args.job_id)
    if job is None:
        print(f"Job {args.job_id} not found.", file=sys.stderr)
        return 1

    work_dir = job.get("work_dir")
    if not work_dir:
        print("Job has no work_dir yet.", file=sys.stderr)
        return 1

    beacon = Path(work_dir) / "PAUSE"
    beacon.touch()
    db.set_status(args.job_id, JobStatus.PAUSED)
    print(f"Job {args.job_id} paused (beacon={beacon})")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    db = _state_db_from_args(args)
    job = db.get_job(args.job_id)
    if job is None:
        print(f"Job {args.job_id} not found.", file=sys.stderr)
        return 1

    work_dir = job.get("work_dir")

    # Re-enqueue the job spec so a worker picks it up again
    queue = JobQueue(args.queue_dir)
    spec = JobSpec(
        job_id=args.job_id,
        config_file=job["config_file"],
        input_xyz=job["input_xyz"],
        submitted_at=job["submitted_at"],
        submitted_by=job.get("submitted_by", "unknown"),
    )
    queue.enqueue(spec)

    # Remove stale PAUSE beacon if work_dir exists
    if work_dir:
        beacon = Path(work_dir) / "PAUSE"
        if beacon.exists():
            beacon.unlink()

    db.set_status(args.job_id, JobStatus.PENDING, error_message=CLEAR, progress_pct=CLEAR, current_step=CLEAR, completed_at=CLEAR)
    print(f"Job {args.job_id} resumed and re-enqueued.")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    db = _state_db_from_args(args)
    job = db.get_job(args.job_id)
    if job is None:
        print(f"Job {args.job_id} not found.", file=sys.stderr)
        return 1

    work_dir = job.get("work_dir")
    if work_dir:
        beacon = Path(work_dir) / "PAUSE"
        beacon.touch()
    db.set_status(args.job_id, JobStatus.CANCELLED)
    print(f"Job {args.job_id} cancelled")
    return 0


# ---------------------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------------------

def cmd_logs(args: argparse.Namespace) -> int:
    log_dir = Path(args.log_dir)
    log_file = log_dir / f"{args.job_id}.log"
    if not log_file.exists():
        # Fallback to agent.log
        agent_log = log_dir / "agent.log"
        if agent_log.exists():
            lines = agent_log.read_text(encoding="utf-8").splitlines()
            relevant = [l for l in lines if args.job_id in l]
            if args.tail:
                relevant = relevant[-args.tail:]
            print("\n".join(relevant))
        else:
            print(f"No log file found for job {args.job_id}", file=sys.stderr)
            return 1
        return 0

    lines = log_file.read_text(encoding="utf-8").splitlines()
    if args.tail:
        lines = lines[-args.tail:]
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------------------

def cmd_stop(args: argparse.Namespace) -> int:
    db = _state_db_from_args(args)
    for job in db.list_jobs(status=JobStatus.RUNNING):
        work_dir = job.get("work_dir")
        if work_dir:
            beacon = Path(work_dir) / "PAUSE"
            beacon.touch()
        db.set_status(job["job_id"], JobStatus.PAUSED)
        print(f"Paused job {job['job_id']}")
    print("All running jobs paused. Agent remains running.")
    return 0


# ---------------------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="confflow-agent", description="ConfFlow Agent daemon")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    p_serve = sub.add_parser("serve", help="Start the agent daemon")
    p_serve.add_argument("--queue-dir", default="~/.confflow-queue", help="Queue directory (default: ~/.confflow-queue)")
    p_serve.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db", help="State DB path")
    p_serve.add_argument("--log-dir", default="~/.local/log/confflow-agent", help="Log directory")
    p_serve.add_argument("--slots", type=int, default=2, help="Number of concurrent slots (default: 2)")
    p_serve.set_defaults(func=cmd_serve)

    # status
    p_status = sub.add_parser("status", help="Show status of a specific job")
    p_status.add_argument("job_id", help="Job ID")
    p_status.add_argument("--queue-dir", default="~/.confflow-queue")
    p_status.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_status.set_defaults(func=cmd_status)

    # list
    p_list = sub.add_parser("list", help="List all jobs")
    p_list.add_argument("--queue-dir", default="~/.confflow-queue")
    p_list.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_list.add_argument("--no-all", dest="no_all", action="store_true",
                       help="Show only pending jobs (default: show all)")
    p_list.set_defaults(func=cmd_list)

    # submit
    p_sub = sub.add_parser("submit", help="Submit a job to the queue")
    p_sub.add_argument("config", help="Path to workflow YAML config")
    p_sub.add_argument("input_xyz", help="Path to input XYZ file")
    p_sub.add_argument("--job-id", help="Custom job ID (default: auto-generated)")
    p_sub.add_argument("--queue-dir", default="~/.confflow-queue")
    p_sub.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_sub.add_argument("--submitted-by", default="cli", help="Submitter identity")
    p_sub.set_defaults(func=cmd_submit)

    # pause
    p_pause = sub.add_parser("pause", help="Pause a running/pending job")
    p_pause.add_argument("job_id", help="Job ID")
    p_pause.add_argument("--queue-dir", default="~/.confflow-queue")
    p_pause.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_pause.set_defaults(func=cmd_pause)

    # resume
    p_resume = sub.add_parser("resume", help="Resume a paused job")
    p_resume.add_argument("job_id", help="Job ID")
    p_resume.add_argument("--queue-dir", default="~/.confflow-queue")
    p_resume.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_resume.set_defaults(func=cmd_resume)

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a job")
    p_cancel.add_argument("job_id", help="Job ID")
    p_cancel.add_argument("--queue-dir", default="~/.confflow-queue")
    p_cancel.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_cancel.set_defaults(func=cmd_cancel)

    # logs
    p_logs = sub.add_parser("logs", help="Show logs for a job")
    p_logs.add_argument("job_id", help="Job ID")
    p_logs.add_argument("--log-dir", default="~/.local/log/confflow-agent")
    p_logs.add_argument("--tail", type=int, metavar="N", help="Show last N lines")
    p_logs.set_defaults(func=cmd_logs)

    # stop
    p_stop = sub.add_parser("stop", help="Pause all running jobs (agent keeps running)")
    p_stop.add_argument("--queue-dir", default="~/.confflow-queue")
    p_stop.add_argument("--state-db", default="~/.local/share/confflow-agent/state.db")
    p_stop.set_defaults(func=cmd_stop)

    return parser


def main(args_list: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(args_list)

    # Expand ~ in paths
    for attr in ("queue_dir", "state_db", "log_dir"):
        if hasattr(args, attr) and isinstance(getattr(args, attr), str):
            setattr(args, attr, os.path.expanduser(getattr(args, attr)))

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
