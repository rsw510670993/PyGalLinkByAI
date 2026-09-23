"""Auditable AI job orchestration for post-submission 115 organization.

The web layer only enqueues and observes jobs.  A background worker invokes a
driver (Codex first), while every state transition and externally visible event
is persisted in egs.db.  Domain mutations remain in the existing organizer
commands so this module never turns model text directly into SQL.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .egs_core import ensure_egs_schema, open_egs_db
from .runtime import read_config, repo_root

JOB_STATES = ("queued", "running", "completed", "partial", "blocked", "failed", "cancelled")
ACTIVE_STATES = ("queued", "running")
FINAL_STATES = tuple(state for state in JOB_STATES if state not in ACTIVE_STATES)
DRIVERS = ("codex",)
MAX_PROMPT_CHARS = 12000
DEFAULT_TIMEOUT_SECONDS = 1800


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_ai_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS egs_ai_jobs (
            job_id          TEXT PRIMARY KEY,
            driver          TEXT NOT NULL,
            prompt          TEXT NOT NULL,
            scope_year      INTEGER,
            status          TEXT NOT NULL DEFAULT 'queued',
            worker_pid      INTEGER,
            agent_pid       INTEGER,
            thread_id       TEXT,
            summary         TEXT,
            result_json     TEXT,
            error           TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            started_at      TEXT,
            updated_at      TEXT NOT NULL,
            finished_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_egs_ai_jobs_status_created
            ON egs_ai_jobs(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS egs_ai_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id          TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            level           TEXT NOT NULL DEFAULT 'info',
            message         TEXT NOT NULL,
            payload         TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES egs_ai_jobs(job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_egs_ai_events_job_id
            ON egs_ai_events(job_id, id);

        CREATE TABLE IF NOT EXISTS egs_ai_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id          TEXT NOT NULL,
            egs_id          INTEGER,
            issue_id        INTEGER,
            decision        TEXT NOT NULL,
            confidence      REAL,
            evidence_json   TEXT,
            actions_json    TEXT,
            requires_human  INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'proposed',
            created_at      TEXT NOT NULL,
            applied_at      TEXT,
            FOREIGN KEY(job_id) REFERENCES egs_ai_jobs(job_id)
        );

        CREATE TABLE IF NOT EXISTS egs_ai_actions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id          TEXT NOT NULL,
            decision_id     INTEGER,
            action_type     TEXT NOT NULL,
            arguments_json  TEXT,
            expected_json   TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            result_json     TEXT,
            created_at      TEXT NOT NULL,
            executed_at     TEXT,
            FOREIGN KEY(job_id) REFERENCES egs_ai_jobs(job_id),
            FOREIGN KEY(decision_id) REFERENCES egs_ai_decisions(id)
        );
        """
    )
    conn.commit()


def _json_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _job_dict(row, include_prompt: bool = True) -> dict:
    data = dict(row)
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    data["result"] = _json_value(data.pop("result_json", None))
    if not include_prompt:
        data.pop("prompt", None)
    return data


def record_event(conn, job_id: str, event_type: str, message: str,
                 *, level: str = "info", payload=None) -> int:
    cur = conn.execute(
        """INSERT INTO egs_ai_events(job_id,event_type,level,message,payload,created_at)
           VALUES (?,?,?,?,?,?)""",
        (job_id, event_type, level, str(message)[:4000],
         json.dumps(payload, ensure_ascii=False) if payload is not None else None, _now()),
    )
    conn.execute("UPDATE egs_ai_jobs SET updated_at=? WHERE job_id=?", (_now(), job_id))
    conn.commit()
    return int(cur.lastrowid)


def create_job(prompt: str, *, driver: str = "codex", scope_year: int | None = None,
               db_path: str | None = None) -> dict:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("任务说明不能为空")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"任务说明不能超过 {MAX_PROMPT_CHARS} 字符")
    if driver not in DRIVERS:
        raise ValueError(f"不支持的 AI driver: {driver}")
    if scope_year is not None and not 1990 <= int(scope_year) <= 2100:
        raise ValueError("年份范围无效")

    conn = open_egs_db(db_path)
    try:
        ensure_ai_schema(conn)
        active = conn.execute(
            "SELECT job_id,status FROM egs_ai_jobs WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone()
        if active:
            raise RuntimeError(f"已有 AI 任务正在处理: {active['job_id']}")
        job_id = uuid.uuid4().hex
        now = _now()
        conn.execute(
            """INSERT INTO egs_ai_jobs
               (job_id,driver,prompt,scope_year,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (job_id, driver, prompt, int(scope_year) if scope_year else None,
             "queued", now, now),
        )
        conn.commit()
        record_event(conn, job_id, "job.queued", "AI 整理任务已进入队列")
        return get_job(job_id, conn=conn)
    finally:
        conn.close()


def get_job(job_id: str, *, conn=None, db_path: str | None = None,
            after_event_id: int = 0) -> dict | None:
    own = conn is None
    if own:
        conn = open_egs_db(db_path)
    try:
        ensure_ai_schema(conn)
        row = conn.execute("SELECT * FROM egs_ai_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = _job_dict(row)
        events = conn.execute(
            """SELECT id,event_type,level,message,payload,created_at
                 FROM egs_ai_events WHERE job_id=? AND id>? ORDER BY id LIMIT 500""",
            (job_id, int(after_event_id or 0)),
        ).fetchall()
        job["events"] = [
            {**dict(event), "payload": _json_value(event["payload"])} for event in events
        ]
        return job
    finally:
        if own:
            conn.close()


def list_jobs(*, limit: int = 30, db_path: str | None = None) -> dict:
    conn = open_egs_db(db_path)
    try:
        ensure_ai_schema(conn)
        rows = conn.execute(
            "SELECT * FROM egs_ai_jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        counts = {
            row["status"]: row["n"] for row in conn.execute(
                "SELECT status,count(*) n FROM egs_ai_jobs GROUP BY status"
            )
        }
        return {"success": True, "jobs": [_job_dict(row, include_prompt=False) for row in rows],
                "counts": counts}
    finally:
        conn.close()


def request_cancel(job_id: str, *, db_path: str | None = None) -> dict:
    conn = open_egs_db(db_path)
    try:
        ensure_ai_schema(conn)
        row = conn.execute("SELECT status FROM egs_ai_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return {"success": False, "message": "任务不存在"}
        if row["status"] in FINAL_STATES:
            return {"success": False, "message": "任务已经结束"}
        conn.execute(
            "UPDATE egs_ai_jobs SET cancel_requested=1,updated_at=? WHERE job_id=?",
            (_now(), job_id),
        )
        conn.commit()
        record_event(conn, job_id, "job.cancel_requested", "已请求停止任务", level="warning")
        return {"success": True, "job_id": job_id}
    finally:
        conn.close()


def driver_status() -> dict:
    binary = shutil.which("codex")
    result = {"driver": "codex", "available": bool(binary), "binary": binary,
              "version": None, "logged_in": None, "message": None}
    if not binary:
        result["message"] = "找不到 codex CLI"
        return result
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        result["version"] = (version.stdout or version.stderr).strip()
        login = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=15)
        result["logged_in"] = login.returncode == 0
        result["message"] = (login.stdout or login.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["message"] = str(exc)
    return result


def system_status(*, db_path: str | None = None) -> dict:
    jobs = list_jobs(limit=10, db_path=db_path)
    return {
        "success": True,
        "driver": driver_status(),
        "jobs": jobs["jobs"],
        "counts": jobs["counts"],
        "policy": "docs/AI_115_ORGANIZER.md",
        "skill": ".agents/skills/egs-115-organizer/SKILL.md",
    }


def _snapshot(conn, scope_year: int | None) -> dict:
    ensure_egs_schema(conn)
    where = "WHERE resolved=0"
    params: list[object] = []
    if scope_year:
        where += " AND substr(date,1,4)=?"
        params.append(str(scope_year))
    issues = []
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "egs_organize_issues" in tables:
        for row in conn.execute(
            f"""SELECT id,egs_id,date,name,status,outcome,message,detail,run_at
                  FROM egs_organize_issues {where} ORDER BY id""", params
        ):
            item = dict(row)
            item["detail"] = _json_value(item.get("detail"))
            issues.append(item)
    game_where = ""
    game_params: list[object] = []
    if scope_year:
        game_where = "WHERE substr(date,1,4)=?"
        game_params.append(str(scope_year))
    totals = dict(conn.execute(
        f"""SELECT count(*) total,
                    sum(COALESCE(submitted_115,0)=1) submitted,
                    sum(COALESCE(downloaded,0)=1) downloaded,
                    sum(COALESCE(download_failed,0)=1) download_failed,
                    sum(COALESCE(magnet_duplicate,0)=1) duplicates
               FROM egs_games {game_where}""", game_params
    ).fetchone())
    return {"scope_year": scope_year, "totals": totals, "open_issues": issues}


def _output_schema_path() -> str:
    return str(Path(repo_root()) / "tool" / "ai_organizer_output.schema.json")


def _build_agent_prompt(job: sqlite3.Row, snapshot: dict) -> str:
    return f"""你是 pyGal 的 115 整理执行代理。只处理 115 提交后的校对、归档和整理，绝不替代 EGS 磁链审核。

必须先完整读取 `.agents/skills/egs-115-organizer/SKILL.md` 和 `docs/AI_115_ORGANIZER.md`，并遵守其中的工具、安全和审计要求。不要修改项目源代码，不要执行 git 命令，不要改动 EGS 磁链审核结论。所有数据操作必须使用项目已有 CLI/业务函数，并在执行后验证。

用户任务：
{job['prompt']}

任务范围快照：
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

完成后严格按输出 schema 返回总结。无法安全完成时设置 requires_human=true，并明确列出缺少的证据，禁止猜测。
"""


def _set_job(conn, job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    sql = ",".join(f"{key}=?" for key in fields)
    conn.execute(f"UPDATE egs_ai_jobs SET {sql} WHERE job_id=?", (*fields.values(), job_id))
    conn.commit()


def store_result_audit(conn, job_id: str, result: dict) -> int:
    """Persist the agent's structured conclusion separately from job transport state."""
    now = _now()
    outcome = str(result.get("outcome") or "partial")
    actions = result.get("actions") if isinstance(result.get("actions"), list) else []
    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    cur = conn.execute(
        """INSERT INTO egs_ai_decisions
           (job_id,decision,evidence_json,actions_json,requires_human,status,created_at,applied_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (job_id, outcome, json.dumps(evidence, ensure_ascii=False),
         json.dumps(actions, ensure_ascii=False), bool(result.get("requires_human")),
         "recorded", now, now),
    )
    decision_id = int(cur.lastrowid)
    for action in actions:
        if not isinstance(action, dict):
            continue
        conn.execute(
            """INSERT INTO egs_ai_actions
               (job_id,decision_id,action_type,arguments_json,status,result_json,created_at,executed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, decision_id, str(action.get("action") or "reported_action"),
             json.dumps({"target": action.get("target")}, ensure_ascii=False),
             "reported", json.dumps({"result": action.get("result")}, ensure_ascii=False),
             now, now),
        )
    conn.commit()
    return decision_id


def _backup_database(conn, job_id: str) -> str:
    target = Path(repo_root()) / "db_backups" / f"egs.before_ai_job_{job_id}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as dst:
        conn.backup(dst)
    return str(target.relative_to(repo_root()))


def run_job(job_id: str, *, db_path: str | None = None,
            timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    conn = open_egs_db(db_path)
    process = None
    try:
        ensure_ai_schema(conn)
        job = conn.execute("SELECT * FROM egs_ai_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job:
            raise RuntimeError("AI 任务不存在")
        if job["status"] != "queued":
            return {"success": False, "message": f"任务状态不是 queued: {job['status']}"}
        _set_job(conn, job_id, status="running", worker_pid=os.getpid(), started_at=_now())
        record_event(conn, job_id, "job.started", "AI 整理任务开始执行")
        backup = _backup_database(conn, job_id)
        record_event(conn, job_id, "database.backup", f"数据库备份已创建：{backup}")

        binary = shutil.which("codex")
        if not binary:
            raise RuntimeError("找不到 codex CLI")
        snapshot = _snapshot(conn, job["scope_year"])
        record_event(conn, job_id, "context.ready", "任务上下文已生成", payload=snapshot)

        status_dir = Path(repo_root()) / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        final_path = status_dir / f"ai_job_{job_id}_final.json"
        command = [
            binary, "--ask-for-approval", "never", "exec", "--cd", repo_root(),
            "--sandbox", "workspace-write", "--color", "never", "--json",
            "--output-schema", _output_schema_path(),
            "--output-last-message", str(final_path), "-",
        ]
        env = os.environ.copy()
        configured_home = str(read_config().get("ai_codex_home") or "").strip()
        if configured_home:
            env["CODEX_HOME"] = configured_home
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, cwd=repo_root(), env=env,
            start_new_session=True, bufsize=1,
        )
        _set_job(conn, job_id, agent_pid=process.pid)
        prompt = _build_agent_prompt(job, snapshot)
        process.stdin.write(prompt)
        process.stdin.close()

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        started = time.monotonic()
        while process.poll() is None:
            cancel = conn.execute(
                "SELECT cancel_requested FROM egs_ai_jobs WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            if cancel:
                process.terminate()
                _set_job(conn, job_id, status="cancelled", finished_at=_now(), agent_pid=None)
                record_event(conn, job_id, "job.cancelled", "任务已停止", level="warning")
                return {"success": True, "status": "cancelled", "job_id": job_id}
            if time.monotonic() - started > int(timeout_seconds):
                process.terminate()
                raise TimeoutError(f"Codex 执行超过 {timeout_seconds} 秒")
            for key, _ in selector.select(timeout=0.5):
                line = key.fileobj.readline()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_type = str(event.get("type") or "agent.event")
                    message = str(event.get("message") or event.get("text") or event_type)
                    if event_type in ("thread.started", "turn.started", "turn.completed", "turn.failed", "error"):
                        record_event(conn, job_id, event_type, message[:1000],
                                     level="error" if event_type in ("turn.failed", "error") else "info",
                                     payload=event)
                    if event_type == "thread.started" and event.get("thread_id"):
                        _set_job(conn, job_id, thread_id=str(event["thread_id"]))
                except ValueError:
                    record_event(conn, job_id, "agent.output", line.strip()[:1000])
        if process.stdout:
            for line in process.stdout:
                if line.strip():
                    record_event(conn, job_id, "agent.output", line.strip()[:1000])
        if process.returncode != 0:
            raise RuntimeError(f"Codex 进程退出码 {process.returncode}")
        if not final_path.exists():
            raise RuntimeError("Codex 未生成结构化最终结果")
        result = json.loads(final_path.read_text(encoding="utf-8"))
        outcome = result.get("outcome") or "completed"
        if outcome not in ("completed", "partial", "blocked", "failed"):
            outcome = "partial"
        store_result_audit(conn, job_id, result)
        _set_job(
            conn, job_id, status=outcome, summary=str(result.get("summary") or ""),
            result_json=json.dumps(result, ensure_ascii=False), finished_at=_now(), agent_pid=None,
        )
        record_event(conn, job_id, "job.finished", result.get("summary") or "AI 整理任务结束",
                     level="error" if outcome == "failed" else "info", payload=result)
        return {"success": outcome == "completed", "status": outcome, "job_id": job_id,
                "result": result}
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.terminate()
        try:
            _set_job(conn, job_id, status="failed", error=str(exc), finished_at=_now(), agent_pid=None)
            record_event(conn, job_id, "job.failed", str(exc), level="error")
        except Exception:
            pass
        return {"success": False, "status": "failed", "job_id": job_id, "message": str(exc)}
    finally:
        conn.close()


def start_job(prompt: str, *, driver: str = "codex", scope_year: int | None = None,
              db_path: str | None = None) -> dict:
    health = driver_status()
    if not health.get("available"):
        raise RuntimeError(health.get("message") or "找不到 codex CLI")
    if health.get("logged_in") is not True:
        raise RuntimeError("当前 Web/Worker 运行账户尚未登录 Codex")
    job = create_job(prompt, driver=driver, scope_year=scope_year, db_path=db_path)
    command = [sys.executable, "-m", "tool.ai_organizer_entry"]
    if db_path:
        command.extend(("--db", db_path))
    command.extend(("worker", "--job-id", job["job_id"]))
    log_dir = Path(repo_root()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / f"ai_organizer_{job['job_id']}.log", "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            command, cwd=repo_root(), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    finally:
        log.close()
    conn = open_egs_db(db_path)
    try:
        ensure_ai_schema(conn)
        _set_job(conn, job["job_id"], worker_pid=proc.pid)
    finally:
        conn.close()
    return {"success": True, "message": "AI 整理任务已启动", "job_id": job["job_id"]}
