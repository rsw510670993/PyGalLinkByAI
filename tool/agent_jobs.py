"""web ↔ 执行器 的任务信箱（spool）与执行器。

为什么用文件而不是 HTTP：web 端点跑在 www-data、执行器跑在 coding，跨用户跨进程；
TUI 又是交互式的（官方不支持 stdout 重定向）。双方都读写的目录是最简单可靠的桥，
而且每个任务天然留下一份 JSON 审计。

  请求  status/agent_jobs/inbox/<job_id>.json      ← web / CLI 写入
  运行  status/agent_jobs/working/<job_id>.json    ← claim 时原子改名（存在即“在跑”）
  结果  status/agent_jobs/outbox/<job_id>.json     ← 执行器写入，web 读取

确定性动作不绕 LLM：crawl / magnet / check / submit 直接转调
`tool.pipeline.start()`；old_audit 调 `gal_old_audit.audit_old_year()` 出四桶报告，
带 `--execute` 时接着调 `clean_old_year(execute=True)` 把「旧⊆新」的冗余删进 115 回收站
（删前现场复验 (路径,SHA1)，同名不同哈希中止该条）。
organize 例外（见 QUEUE_ONLY_ACTIONS）：网页只入队、不派生 worker，
由 TUI 侧的 agent 领取处理（`python -m tool.agent_jobs run --once`，或按
egs-115-organizer skill 自行执行）。

用法：
    python -m tool.agent_jobs start   --action crawl --year 2020 [--execute]   # 入队并派生 worker
    python -m tool.agent_jobs enqueue --action crawl --year 2020               # 只入队
    python -m tool.agent_jobs run --job-id <id>                                # 前台跑一个
    python -m tool.agent_jobs run --once                                       # 领一个就跑
    python -m tool.agent_jobs status --job-id <id>
    python -m tool.agent_jobs list [--limit 20]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .pipeline import ACTIONS as PIPELINE_ACTIONS
from .pipeline import LABELS as PIPELINE_LABELS
from .runtime import daily_log_path, now_ts, pid_is_running, read_json, repo_root, runtime_paths, write_json_atomic

AGENT_ACTION = 'old_audit'
CLEAN_ACTION = 'old_clean'
ALL_ACTIONS = tuple(PIPELINE_ACTIONS) + (AGENT_ACTION, CLEAN_ACTION)
# organize 是唯一需要人在环的动作（搬月 / 合集归属 / 旧库判定）：网页只入队，
# 不派生后台 worker，由 TUI 侧领取执行。
QUEUE_ONLY_ACTIONS = ('organize',)
LABELS = dict(PIPELINE_LABELS)
LABELS[AGENT_ACTION] = '核对旧库'
LABELS[CLEAN_ACTION] = '清理旧库(冗余候选)'

POLL_SECONDS = 5
MAX_WAIT_SECONDS = 6 * 60 * 60          # 单次 pipeline 动作最长等待
STALL_SECONDS = 600                     # 状态文件长时间不前进且进程已消失 → 判为中断
# 硬超时：p115client 可能在自动 relogin 循环里永久阻塞（无超时的 HTTP），
# 用 SIGALRM 兜底，避免留下永远 queued/running 的僵尸任务。
# old_audit 是重 API 动作（每条两次递归 walk），给 2 小时；它可续跑，超时也不丢成果。
DEFAULT_TIMEOUTS = {AGENT_ACTION: 2 * 60 * 60, CLEAN_ACTION: 2 * 60 * 60,
                    'submit': 3 * 60 * 60, 'magnet': 2 * 60 * 60}
FALLBACK_TIMEOUT = 60 * 60


class JobTimeout(Exception):
    """SIGALRM 兜底触发。"""


def _alarm_handler(_signum, _frame):
    raise JobTimeout()


def _now_iso():
    return datetime.datetime.now().isoformat(timespec='seconds')


def jobs_root() -> Path:
    root = Path(runtime_paths()['status_dir']) / 'agent_jobs'
    return root


def _dirs(root: Path):
    return {name: root / name for name in ('inbox', 'working', 'outbox')}


def ensure_dirs(root: Path | None = None):
    """建 spool 目录。web(www-data) 与执行器(coding) 都要读写，故开放到 0777。"""
    root = Path(root or jobs_root())
    dirs = _dirs(root)
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o777)
        except OSError:
            pass
    return dirs


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(str(path), payload)
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def _job_id(year: int | None) -> str:
    return f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{str(year or 0)}-{uuid.uuid4().hex[:6]}"


def enqueue(action: str, year: int, year_end: int | None = None, dry_run: bool = False,
            requested_by: str = 'web', root: Path | None = None) -> dict:
    """把一条任务写进 inbox，返回请求体（含 job_id）。"""
    if action not in ALL_ACTIONS:
        raise ValueError(f'未知动作 {action}，可选：{", ".join(ALL_ACTIONS)}')
    year = int(year)
    year_end = int(year_end or year)
    if year_end < year:
        raise ValueError('end 年不能小于 start 年')
    dirs = ensure_dirs(root)
    job = {
        'job_id': _job_id(year), 'action': action, 'label': LABELS.get(action, action),
        'year': year, 'year_end': year_end, 'dry_run': bool(dry_run),
        'requested_by': requested_by, 'created_at': _now_iso(), 'status': 'queued',
    }
    _write_json(dirs['inbox'] / f"{job['job_id']}.json", job)
    return job


def read_job(job_id: str, root: Path | None = None) -> dict | None:
    dirs = _dirs(Path(root or jobs_root()))
    for name in ('outbox', 'working', 'inbox'):
        path = dirs[name] / f'{job_id}.json'
        if path.exists():
            payload = read_json(str(path), {}) or {}
            payload['_state'] = {'outbox': 'finished', 'working': 'running', 'inbox': 'queued'}[name]
            payload['_path'] = str(path.relative_to(Path(root or jobs_root()).parent))
            return payload
    return None


def list_jobs(limit: int = 20, root: Path | None = None) -> list:
    dirs = _dirs(Path(root or jobs_root()))
    found = []
    for name, state in (('outbox', 'finished'), ('working', 'running'), ('inbox', 'queued')):
        for path in dirs[name].glob('*.json'):
            payload = read_json(str(path), {}) or {}
            payload['_state'] = state
            payload['_mtime'] = path.stat().st_mtime
            found.append(payload)
    found.sort(key=lambda item: str(item.get('job_id') or ''), reverse=True)
    return found[: max(1, int(limit))]


def claim(job_id: str | None = None, root: Path | None = None) -> dict | None:
    """取一个待办并原子改名到 working/（存在即代表在跑）。"""
    dirs = ensure_dirs(root)
    if job_id:
        src = dirs['inbox'] / f'{job_id}.json'
        if not src.exists():
            return None
    else:
        candidates = sorted(dirs['inbox'].glob('*.json'))
        if not candidates:
            return None
        src = candidates[0]
    dst = dirs['working'] / src.name
    os.replace(src, dst)
    job = read_json(str(dst), {}) or {}
    job['_state'] = 'running'
    return job


def _result(job: dict, ok: bool, status: str, counts=None, highlights=None,
            artifacts=None, next_actions=None, error=None, extra=None) -> dict:
    now = datetime.datetime.now()
    payload = {
        'job_id': job.get('job_id'), 'action': job.get('action'), 'label': job.get('label'),
        'year': job.get('year'), 'year_end': job.get('year_end'), 'dry_run': job.get('dry_run'),
        'ok': bool(ok), 'status': status,
        'started_at': job.get('started_at') or now.isoformat(timespec='seconds'),
        'finished_at': now.isoformat(timespec='seconds'),
        'counts': counts or {}, 'artifacts': artifacts or [],
        'highlights': highlights or [], 'next_actions': next_actions or [], 'error': error,
    }
    if extra:
        payload.update(extra)
    try:                                   # 供页面显示耗时；解析不出就留空
        started = datetime.datetime.fromisoformat(str(payload['started_at']))
        payload['duration_s'] = max(0, int((now - started).total_seconds()))
    except (TypeError, ValueError):
        pass
    return payload


def _run_pipeline_action(job: dict) -> dict:
    """crawl/magnet/check/submit/organize → 转调 pipeline.start 并等到结束。"""
    from . import pipeline

    action = job['action']
    result = pipeline.start(action, int(job['year']), int(job['year_end']), 0, not job.get('dry_run', False))
    if not isinstance(result, dict) or result.get('status') == 'error':
        message = (result or {}).get('message') or 'pipeline 启动失败'
        return _result(job, False, 'error', error=message,
                       highlights=[f'pipeline 拒绝启动：{message}'])
    pipeline_job = result.get('job_id')
    deadline = time.time() + MAX_WAIT_SECONDS
    state = {}
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        state = pipeline.status() or {}
        if state.get('job_id') != pipeline_job:
            break
        if not state.get('running'):
            break
        age = now_ts() - int(state.get('updated_at') or 0)
        pid = int(state.get('pid') or 0)
        if age > STALL_SECONDS and pid and not pid_is_running(pid):
            state.update(running=False, outcome='stalled', message='执行进程已退出，请查看日志')
            break
    counts = {
        'total': state.get('total', 0), 'success': state.get('success', 0),
        'failed': state.get('failed', 0), 'skipped': state.get('skipped', 0),
        'done': state.get('done', 0),
    }
    outcome = state.get('outcome') or 'unknown'
    ok = outcome == 'complete' and not counts['failed']
    status = 'complete' if ok else ('partial' if counts['success'] else 'error')
    highlights = [f"pipeline {action} {job['year']}-{job['year_end']}："
                  f"{counts['done']}/{counts['total']}，成功 {counts['success']}，"
                  f"失败 {counts['failed']}，跳过 {counts['skipped']}（{outcome}）"]
    if state.get('message'):
        highlights.append(str(state['message']))
    return _result(job, ok, status, counts=counts, highlights=highlights,
                   extra={'pipeline_job_id': pipeline_job, 'pipeline_outcome': outcome,
                          'label': LABELS.get(action, action)})


def _run_old_clean(job: dict, root: Path | None = None) -> dict:
    from .gal_old_audit import clean_old_year

    def progress(done, total, current):
        job['progress'] = {'done': done, 'total': total, 'current': str(current)[:80]}
        _update_working(job, root)

    # 清理动作：dry_run 默认 True → 只预览；要真删必须显式 execute
    payload = clean_old_year(int(job['year']), execute=not job.get('dry_run', True),
                             progress=progress, deadline=job.get('deadline_ts'))
    counts = dict(payload.get('counts') or {})
    ok = bool(payload.get('ok', True)) and payload.get('status') != 'partial'
    return _result(job, ok, payload.get('status') or 'complete', counts=counts,
                   highlights=payload.get('highlights') or [],
                   artifacts=payload.get('artifacts') or [], error=payload.get('error'))


def _run_old_audit(job: dict, root: Path | None = None) -> dict:
    from .gal_old_audit import audit_old_year, clean_old_year

    def progress(done, total, current):
        job['progress'] = {'done': done, 'total': total, 'current': str(current)[:80]}
        _update_working(job, root)

    payload = audit_old_year(int(job['year']), progress=progress,
                             deadline=job.get('deadline_ts'))
    counts = dict(payload.get('counts') or {})
    ok = bool(payload.get('ok', True)) and payload.get('status') != 'partial'
    highlights = list(payload.get('highlights') or [])
    if payload.get('status') == 'partial':
        highlights.append('本次未审完（结果已落盘，再点一次会接着审）')
    extra = {'detail_manifest': (payload.get('artifacts') or [None])[0],
             'state_file': payload.get('state_file')}

    # 「真正执行」= 核对完当场按手顺删「旧⊆新」的冗余（clean_old_year：删前现场复验、
    # 进 115 回收站、同名不同哈希中止）。默认 dry-run 只出报告，一条不动。
    if not job.get('dry_run', True):
        if payload.get('status') == 'partial':
            highlights.append('未审完，本轮跳过删除（先看预览结论，下次点一次会接着审）')
        else:
            cleaned = clean_old_year(int(job['year']), execute=True, progress=progress,
                                     deadline=job.get('deadline_ts'))
            clean_counts = dict(cleaned.get('counts') or {})
            for key in ('candidates', 'verified', 'deleted', 'verify_failed', 'delete_failed'):
                if key in clean_counts:
                    counts['clean_' + key] = clean_counts[key]
            extra['clean_status'] = cleaned.get('status')
            if cleaned.get('error'):
                extra['clean_error'] = cleaned['error']
                highlights.append(str(cleaned['error']))
            highlights.append(
                '按手顺删冗余：候选 {} 条 → 复验通过 {} / 删进回收站 {} / 复验未过 {} / 删除失败 {}'
                .format(clean_counts.get('candidates', 0), clean_counts.get('verified', 0),
                        clean_counts.get('deleted', 0), clean_counts.get('verify_failed', 0),
                        clean_counts.get('delete_failed', 0)))
            ok = ok and bool(cleaned.get('ok', True))

    return _result(job, ok, payload.get('status') or 'complete',
                   counts=counts, highlights=highlights,
                   artifacts=payload.get('artifacts') or [], error=payload.get('error'),
                   extra=extra)


def timeout_for(action: str, override: int | None = None) -> int:
    if override:
        return int(override)
    return int(DEFAULT_TIMEOUTS.get(action, FALLBACK_TIMEOUT))


def _update_working(job: dict, root: Path | None = None):
    """把内存里的进度/pid 回写到 working/，供状态查询与 stop 定位进程。"""
    path = _dirs(Path(root or jobs_root()))['working'] / f"{job['job_id']}.json"
    if path.exists():
        _write_json(path, {k: v for k, v in job.items() if not k.startswith('_')})


def run_job(job: dict, root: Path | None = None, timeout_s: int | None = None) -> dict:
    """执行一条任务并把结果写进 outbox；带 SIGALRM 硬超时兜底。"""
    import signal

    job = dict(job)
    job.setdefault('started_at', _now_iso())
    limit = timeout_for(job.get('action'), timeout_s)
    job['deadline_ts'] = time.time() + limit      # 供可续跑动作自己收尾（如 old_audit）
    payload = None
    try:
        if hasattr(signal, 'SIGALRM'):
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(limit)
        if job['action'] == AGENT_ACTION:
            payload = _run_old_audit(job, root)
        elif job['action'] == CLEAN_ACTION:
            payload = _run_old_clean(job, root)
        else:
            payload = _run_pipeline_action(job)
    except JobTimeout:
        payload = _result(job, False, 'error',
                          error=f'超时：执行超过 {limit}s 仍未返回（多半卡在 115 请求重试），已中止')
    except Exception as exc:                      # noqa: BLE001 - 结果契约要吞掉异常
        payload = _result(job, False, 'error', error=f'{type(exc).__name__}: {exc}')
    finally:
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)
    dirs = ensure_dirs(root)
    _write_json(dirs['outbox'] / f"{job['job_id']}.json", payload)
    working = dirs['working'] / f"{job['job_id']}.json"
    if working.exists():
        working.unlink()
    return payload


def stop(job_id: str, root: Path | None = None) -> dict:
    """终止正在运行的任务：按 working/ 里记录的 pid 发信号，然后归档为 cancelled。"""
    import signal

    dirs = ensure_dirs(root)
    job = read_job(job_id, root)
    if not job or job.get('_state') != 'running':
        return {'status': 'error', 'message': '任务不在运行中'}
    pid = int(job.get('pid') or 0)
    note = '已请求停止'
    if pid:
        signalled = False
        for sig, label in ((signal.SIGTERM, 'SIGTERM'), (signal.SIGKILL, 'SIGKILL')):
            try:
                os.kill(pid, sig)
                signalled = True
                note = f'已终止 pid {pid}（{label}）'
            except ProcessLookupError:
                # 第一次就 ESRCH = 进程早已退出；第二次 ESRCH 说明 SIGTERM 已生效，
                # 不能覆盖掉“已终止(SIGTERM)”这句更有信息量的话。
                if not signalled:
                    note = f'pid {pid} 已不存在（进程已自行退出）'
                break
            except PermissionError:
                return {'status': 'error',
                        'message': f'无权限终止 pid {pid}（执行器与当前调用者不是同一用户）'}
            time.sleep(2)
    payload = _result(job, False, 'cancelled', error=note)
    _write_json(dirs['outbox'] / f"{job_id}.json", payload)
    working = dirs['working'] / f"{job_id}.json"
    if working.exists():
        working.unlink()
    return {'status': 'success', 'message': note, 'job_id': job_id}


def start(action: str, year: int, year_end: int | None = None, dry_run: bool = False,
          requested_by: str = 'web', timeout_s: int | None = None) -> dict:
    """网页/CLI 手动启动：入队 + 派生后台 worker（不依赖 TUI）。

    QUEUE_ONLY_ACTIONS（organize）只入队、不派生 worker —— 由 TUI 侧领取执行。
    """
    try:
        job = enqueue(action, year, year_end, dry_run, requested_by)
    except ValueError as exc:
        return {'status': 'error', 'message': str(exc)}
    if action in QUEUE_ONLY_ACTIONS:
        return {'status': 'success',
                'message': '已入队，等待 TUI 侧领取执行（python -m tool.agent_jobs run --once）',
                'job_id': job['job_id'], 'action': action, 'year': year, 'label': job['label'],
                'queued_only': True}
    log = daily_log_path('agent_jobs')
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    try:
        output = open(log, 'ab', buffering=0)
    except PermissionError:
        os.remove(log)
        output = open(log, 'ab', buffering=0)
    try:
        os.chmod(log, 0o666)
    except OSError:
        pass
    cmd = [sys.executable, '-m', 'tool.agent_jobs', 'worker', '--job-id', job['job_id']]
    if timeout_s:
        cmd += ['--timeout', str(int(timeout_s))]
    with output:
        worker = subprocess.Popen(cmd, cwd=repo_root(), stdout=output, stderr=output,
                                  start_new_session=True)
    job['pid'] = worker.pid
    return {'status': 'success', 'message': '任务已启动', 'job_id': job['job_id'],
            'action': action, 'year': year, 'label': job['label'],
            'timeout_s': timeout_for(action, timeout_s)}


def _dump(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write('\n')


# ---- 维护：清理任务记录与 db_backups 旧快照 -------------------------------

DB_SNAPSHOT_GLOBS = ('egs.before_*.db', 'egs.db.pre-*')
HISTORY_NAME = 'maintenance_history.jsonl'


def _human_bytes(n) -> str:
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def _file_entry(path: Path) -> dict:
    st = path.stat()
    return {'name': path.name, 'path': str(path), 'size': int(st.st_size), 'mtime': float(st.st_mtime)}


def _sorted_entries(paths) -> list:
    entries = [_file_entry(p) for p in paths if p.is_file()]
    entries.sort(key=lambda e: e['mtime'], reverse=True)
    return entries


def _pick_doomed(entries: list, keep: int, days: int) -> list:
    """entries 已按 mtime 新→旧排序：保留最新 keep 个 + 最近 days 天，其余待删。"""
    cutoff = time.time() - max(0, int(days)) * 86400
    keep = max(0, int(keep))
    return [e for idx, e in enumerate(entries) if idx >= keep and e['mtime'] < cutoff]


def _summarize(entries, doomed) -> dict:
    return {
        'total': len(entries),
        'delete': len(doomed),
        'keep': len(entries) - len(doomed),
        'total_bytes': sum(e['size'] for e in entries),
        'delete_bytes': sum(e['size'] for e in doomed),
        'oldest_to_delete': [e['name'] for e in reversed(doomed)][:5],
        'newest_kept': [e['name'] for e in entries[:3]],
    }


def _append_history(result: dict, history_path=None):
    """执行清理才写履历（预览不写），一行一条 JSON，供网页展示。"""
    path = Path(history_path) if history_path else (
        Path(runtime_paths()['status_dir']) / HISTORY_NAME)
    entry = {
        'time': _now_iso(),
        'mode': 'execute' if result.get('execute') else 'preview',
        'params': result.get('params'),
        'spool': result.get('spool'),
        'db_backups': result.get('db_backups'),
        'deleted': result.get('deleted'),
        'freed_bytes': result.get('freed_bytes'),
        'errors': result.get('errors') or [],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass
    except OSError:
        pass


def prune(keep: int = 50, days: int = 14, db_keep: int = 3, db_days: int = 7,
          execute: bool = False, requested_by: str = 'cli', root: Path | None = None,
          db_dir=None, history_path=None) -> dict:
    """清理任务 spool（只动 outbox）与 db_backups 的数据库快照：默认只预览。

    保留规则：spool 最新 keep 个 + 最近 days 天；db 快照最新 db_keep 个 + 最近 db_days 天。
    inbox/working 永不删；审计缓存 gal_old_audit_*.json 与其它清单 JSON 默认不删。
    """
    db_root = Path(db_dir or (Path(repo_root()) / 'db_backups'))
    spool = _sorted_entries(_dirs(Path(root or jobs_root()))['outbox'].glob('*.json'))
    dbs = []
    for pattern in DB_SNAPSHOT_GLOBS:
        dbs.extend(_sorted_entries(db_root.glob(pattern)))
    dbs.sort(key=lambda e: e['mtime'], reverse=True)
    spool_doomed = _pick_doomed(spool, keep, days)
    db_doomed = _pick_doomed(dbs, db_keep, db_days)

    deleted, errors = [], []
    if execute:
        for ent in spool_doomed + db_doomed:
            try:
                Path(ent['path']).unlink()
                deleted.append(ent['name'])
            except OSError as exc:
                errors.append(f"{ent['name']}: {exc}")

    spool_sum = _summarize(spool, spool_doomed)
    db_sum = _summarize(dbs, db_doomed)
    pending_bytes = spool_sum['delete_bytes'] + db_sum['delete_bytes']
    freed = pending_bytes if execute else 0
    status_dir = Path(runtime_paths()['status_dir'])
    audit_files = [p for p in status_dir.glob('gal_old_audit_*.json') if p.is_file()]
    log_dir = Path(runtime_paths()['log_dir'])
    log_files = [p for p in log_dir.glob('*.log') if p.is_file()] if log_dir.is_dir() else []
    result = {
        'status': 'success',
        'execute': bool(execute),
        'requested_by': requested_by,
        'params': {'keep': keep, 'days': days, 'db_keep': db_keep, 'db_days': db_days},
        'spool': spool_sum,
        'db_backups': db_sum,
        'inventory': {
            'db_dir': str(db_root),
            'spool_bytes': spool_sum['total_bytes'],
            'db_count': db_sum['total'],
            'db_bytes': db_sum['total_bytes'],
            'audit_cache_count': len(audit_files),
            'audit_cache_bytes': sum(p.stat().st_size for p in audit_files),
            'log_count': len(log_files),
            'log_bytes': sum(p.stat().st_size for p in log_files),
        },
        'deleted': len(deleted) if execute else 0,
        'freed_bytes': freed,
        'errors': errors,
    }
    result['highlights'] = [
        ('已删除' if execute else '预览：将删除') +
        f" {spool_sum['delete'] + db_sum['delete']} 个文件（{_human_bytes(pending_bytes)}）"
        f"：任务记录 {spool_sum['delete']} 个、db 快照 {db_sum['delete']} 个",
        f"保留：任务记录最新 {keep} 个 + 最近 {days} 天；db 快照最新 {db_keep} 个 + 最近 {db_days} 天",
    ]
    if not execute:
        result['highlights'].append('这只是预览，未删任何文件；真正执行用 --execute 或网页「执行清理」')
    if errors:
        result['highlights'].append(f'{len(errors)} 个删除失败，见 errors')
    if execute:
        _append_history(result, history_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='任务信箱与执行器')
    sub = parser.add_subparsers(dest='command')

    p_start = sub.add_parser('start', help='入队并派生后台 worker')
    p_start.add_argument('--action', required=True, choices=ALL_ACTIONS)
    p_start.add_argument('--year', type=int, required=True)
    p_start.add_argument('--year-end', type=int, default=None)
    p_start.add_argument('--execute', action='store_true', help='真正执行（默认 dry-run）')
    p_start.add_argument('--requested-by', default='web')
    p_start.add_argument('--timeout', type=int, default=None, help='硬超时秒数（默认按动作）')

    p_enq = sub.add_parser('enqueue', help='只入队，不派生 worker')
    p_enq.add_argument('--action', required=True, choices=ALL_ACTIONS)
    p_enq.add_argument('--year', type=int, required=True)
    p_enq.add_argument('--year-end', type=int, default=None)
    p_enq.add_argument('--execute', action='store_true')
    p_enq.add_argument('--requested-by', default='cli')

    p_run = sub.add_parser('run', help='前台执行一个任务')
    p_run.add_argument('--job-id', default=None)
    p_run.add_argument('--once', action='store_true', help='领取一个待办并执行')
    p_run.add_argument('--timeout', type=int, default=None, help='硬超时秒数（默认按动作）')

    p_worker = sub.add_parser('worker', help='后台 worker（由 start 派生）')
    p_worker.add_argument('--job-id', required=True)
    p_worker.add_argument('--timeout', type=int, default=None)

    p_stop = sub.add_parser('stop', help='终止正在运行的任务')
    p_stop.add_argument('--job-id', required=True)

    p_status = sub.add_parser('status')
    p_status.add_argument('--job-id', required=True)

    p_list = sub.add_parser('list')
    p_list.add_argument('--limit', type=int, default=20)

    p_prune = sub.add_parser('prune', help='清理任务记录与 db 快照（默认只预览）')
    p_prune.add_argument('--keep', type=int, default=50, help='任务记录保留最新 N 个')
    p_prune.add_argument('--days', type=int, default=14, help='任务记录保留最近 N 天')
    p_prune.add_argument('--db-keep', type=int, default=3, help='db 快照保留最新 N 个')
    p_prune.add_argument('--db-days', type=int, default=7, help='db 快照保留最近 N 天')
    p_prune.add_argument('--requested-by', default='cli')
    p_prune.add_argument('--execute', action='store_true', help='真正删除（默认预览）')

    args = parser.parse_args(argv)
    if args.command == 'start':
        _dump(start(args.action, args.year, args.year_end, not args.execute,
                    args.requested_by, args.timeout))
    elif args.command == 'enqueue':
        _dump(enqueue(args.action, args.year, args.year_end, not args.execute, args.requested_by))
    elif args.command in ('run', 'worker'):
        if args.job_id:
            job = claim(args.job_id)
            if job is None:
                _dump({'status': 'error', 'message': f"待办不存在或已被领取：{args.job_id}"})
                return 1
        elif args.once:
            job = claim()
            if job is None:
                _dump({'status': 'success', 'message': '没有待办', 'job_id': None})
                return 0
        else:
            _dump({'status': 'error', 'message': '需要 --job-id 或 --once'})
            return 1
        job['pid'] = os.getpid()          # 供 stop 定位进程
        _update_working(job)
        _dump(run_job(job, timeout_s=args.timeout))
    elif args.command == 'stop':
        _dump(stop(args.job_id))
    elif args.command == 'status':
        payload = read_job(args.job_id)
        _dump(payload or {'status': 'error', 'message': f'找不到任务 {args.job_id}'})
        return 0 if payload else 1
    elif args.command == 'list':
        _dump({'status': 'success', 'count': len(list_jobs(args.limit)), 'jobs': list_jobs(args.limit)})
    elif args.command == 'prune':
        _dump(prune(args.keep, args.days, args.db_keep, args.db_days,
                    args.execute, args.requested_by))
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
