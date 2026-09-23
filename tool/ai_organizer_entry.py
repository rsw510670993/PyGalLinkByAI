#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from tool.ai_organizer import (
    get_job, list_jobs, request_cancel, run_job, start_job, system_status, year_preview,
)


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    preview = sub.add_parser("year-preview")
    preview.add_argument("--year", type=int, required=True)
    jobs = sub.add_parser("jobs")
    jobs.add_argument("--limit", type=int, default=30)
    detail = sub.add_parser("detail")
    detail.add_argument("--job-id", required=True)
    detail.add_argument("--after-event-id", type=int, default=0)
    start = sub.add_parser("start")
    start.add_argument("--prompt", required=True)
    start.add_argument("--driver", choices=("codex",), default="codex")
    start.add_argument("--year", type=int)
    start.add_argument("--egs-ids", default="")
    stop = sub.add_parser("stop")
    stop.add_argument("--job-id", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--job-id", required=True)
    worker.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    try:
        if args.command == "status":
            result = system_status(db_path=args.db)
        elif args.command == "year-preview":
            result = year_preview(args.year, db_path=args.db)
        elif args.command == "jobs":
            result = list_jobs(limit=args.limit, db_path=args.db)
        elif args.command == "detail":
            job = get_job(args.job_id, db_path=args.db, after_event_id=args.after_event_id)
            result = {"success": bool(job), "job": job, "message": None if job else "任务不存在"}
        elif args.command == "start":
            egs_ids = [int(value) for value in args.egs_ids.split(",") if value.strip()]
            result = start_job(
                args.prompt, driver=args.driver, scope_year=args.year,
                scope_egs_ids=egs_ids, db_path=args.db,
            )
        elif args.command == "stop":
            result = request_cancel(args.job_id, db_path=args.db)
        else:
            result = run_job(args.job_id, db_path=args.db, timeout_seconds=args.timeout)
        emit(result)
    except Exception as exc:
        emit({"success": False, "message": str(exc)})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
