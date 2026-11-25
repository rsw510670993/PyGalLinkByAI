"""原 agent_jobs.prune 搬迁（2026-11-25）：清理任务 spool 与 db_backups 快照。

agent_jobs 文件信箱（inbox/working/outbox）已退役，只留下这里的维护清理。
逻辑与返回字段与原 tool/agent_jobs.py 的 prune 完全一致：默认只预览，
保留最新 N 个 + 最近 N 天，inbox/working 永不删。
"""
from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path

from .runtime import repo_root, runtime_paths

DB_SNAPSHOT_GLOBS = ('egs.before_*.db', 'egs.db.pre-*')
HISTORY_NAME = 'maintenance_history.jsonl'


def _now_iso():
    return datetime.datetime.now().isoformat(timespec='seconds')


def jobs_root() -> Path:
    root = Path(runtime_paths()['status_dir']) / 'agent_jobs'
    return root


def _dirs(root: Path):
    return {name: root / name for name in ('inbox', 'working', 'outbox')}


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
