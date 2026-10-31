"""Getchu 封面下载：把封面包图存到 thumbnails/{getchu_id}.jpg。

只处理已经有 getchu_id 的记录（EGS 抓取时带回来的 id），按 id 直连带图，
零搜索、零误配；没有 getchu_id 的记录留给后续「按名搜索」阶段。
下载是幂等的：目标文件已存在就跳过，可随时中断续跑。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from .egs_core import ensure_egs_schema, open_egs_db
from .runtime import now_ts, repo_root, write_json_atomic

COVER_URL = 'https://www.getchu.com/brandnew/{gid}/c{gid}package.jpg'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
STATE_NAME = 'thumbs_state.json'
MIN_BYTES = 4096


def thumb_dir() -> Path:
    return Path(repo_root()) / 'thumbnails'


def thumb_path(gid) -> Path:
    return thumb_dir() / f'{gid}.jpg'


def pending_rows(conn, year: int | None = None, month: int | None = None,
                 limit: int = 0) -> list:
    """有 getchu_id 但本地缺图的记录。"""
    sql = ("SELECT egs_id, getchu_id, date, name FROM egs_games "
           "WHERE getchu_id IS NOT NULL AND getchu_id != ''")
    params: list = []
    if year:
        if month:
            sql += ' AND date = ?'
            params.append(f'{int(year):04d}-{int(month):02d}')
        else:
            sql += ' AND date >= ? AND date < ?'
            params.extend([f'{int(year):04d}-01', f'{int(year) + 1:04d}-01'])
    sql += ' ORDER BY date, egs_id'
    rows = conn.execute(sql, params).fetchall()
    missing = [r for r in rows if not thumb_path(r['getchu_id']).is_file()]
    return missing[:limit] if limit else missing


def download_cover(gid, session: requests.Session, timeout: int = 30):
    """返回 (ok, message, bytes)。"""
    url = COVER_URL.format(gid=gid)
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return False, f'网络错误: {exc}', 0
    if resp.status_code != 200:
        return False, f'HTTP {resp.status_code}', 0
    ctype = resp.headers.get('Content-Type', '')
    if 'image' not in ctype:
        return False, f'非图片: {ctype}', 0
    if len(resp.content) < MIN_BYTES:
        return False, '文件过小', len(resp.content)
    target = thumb_path(gid)
    tmp = target.with_name(target.name + '.part')
    tmp.write_bytes(resp.content)
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    tmp.replace(target)
    return True, f'{len(resp.content)} bytes', len(resp.content)


def run_thumbs(year: int | None = None, month: int | None = None, limit: int = 0,
               interval: float = 1.0, should_stop=None, logger=None) -> dict:
    log = logger or (lambda msg: None)
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = pending_rows(conn, year, month, limit)
    finally:
        conn.close()

    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Referer': 'https://www.getchu.com/'})

    stats = {
        'status': 'success',
        'mode': 'getchu_id',
        'year': year,
        'month': month,
        'interval': interval,
        'total': len(rows),
        'ok': 0,
        'failed': 0,
        'failed_ids': [],
        'started_at': now_ts(),
        'finished_at': None,
        'last_error': '',
    }
    log(f'待下载 {len(rows)} 张封面')
    for i, row in enumerate(rows):
        if should_stop and should_stop():
            stats['status'] = 'stopped'
            break
        gid = str(row['getchu_id']).strip()
        ok, message, _size = download_cover(gid, session)
        if ok:
            stats['ok'] += 1
            log(f'[{i + 1}/{len(rows)}] {gid} ✓ {message} {row["name"]}')
        else:
            stats['failed'] += 1
            stats['failed_ids'].append(gid)
            stats['last_error'] = message
            log(f'[{i + 1}/{len(rows)}] {gid} ✗ {message} {row["name"]}')
        if interval and i + 1 < len(rows):
            time.sleep(interval)

    stats['finished_at'] = now_ts()
    _save_state(stats)
    return stats


def _save_state(stats: dict) -> None:
    try:
        root = Path(repo_root()) / 'status'
        root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(root / STATE_NAME), stats)
    except OSError:
        pass


def coverage() -> dict:
    """封面覆盖率统计（只读，供网页/CLI 查询）。"""
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = conn.execute(
            "SELECT getchu_id, date FROM egs_games "
            "WHERE getchu_id IS NOT NULL AND getchu_id != ''"
        ).fetchall()
    finally:
        conn.close()
    have = sum(1 for r in rows if thumb_path(r['getchu_id']).is_file())
    return {'with_id': len(rows), 'with_cover': have, 'missing': len(rows) - have}

# ---------------- DLsite 封面（非 Getchu 收录的同人/商业作品） ----------------
DLSITE_URL = 'https://www.dlsite.com/{site}/work/=/product_id/{pid}.html'
DLSITE_SITE = {'VJ': 'pro', 'RJ': 'maniax'}
DLSITE_UA = UA
OG_IMAGE_RE = r'property="og:image"\s+content="([^"]+)"'


def dlsite_dir() -> Path:
    return thumb_dir() / 'dlsite'


def dlsite_thumb_path(pid) -> Path:
    return dlsite_dir() / f'{pid}.jpg'


def dlsite_site(pid: str):
    prefix = ''.join(ch for ch in str(pid) if ch.isalpha()).upper()
    return DLSITE_SITE.get(prefix)


def has_getchu_cover(row) -> bool:
    gid = str(row['getchu_id'] or '').strip()
    return bool(gid) and thumb_path(gid).is_file()


def pending_dlsite(conn, year: int | None = None, month: int | None = None,
                   limit: int = 0) -> list:
    """有 dlsite_id、但 getchu 图缺失且 dlsite 图也缺失的记录。"""
    sql = ("SELECT egs_id, getchu_id, dlsite_id, date, name FROM egs_games "
           "WHERE dlsite_id IS NOT NULL AND dlsite_id != ''")
    params: list = []
    if year:
        if month:
            sql += ' AND date = ?'
            params.append(f'{int(year):04d}-{int(month):02d}')
        else:
            sql += ' AND date >= ? AND date < ?'
            params.extend([f'{int(year):04d}-01', f'{int(year) + 1:04d}-01'])
    sql += ' ORDER BY date, egs_id'
    rows = conn.execute(sql, params).fetchall()
    missing = [
        r for r in rows
        if dlsite_site(r['dlsite_id'])
        and not has_getchu_cover(r)
        and not dlsite_thumb_path(r['dlsite_id']).is_file()
    ]
    return missing[:limit] if limit else missing


def download_dlsite(pid, session: requests.Session, timeout: int = 30):
    """访问作品页取 og:image 再下载；返回 (ok, message, bytes)。"""
    import re

    site = dlsite_site(pid)
    if not site:
        return False, '未知 dlsite 前缀', 0
    try:
        page = session.get(DLSITE_URL.format(site=site, pid=pid), timeout=timeout)
    except requests.RequestException as exc:
        return False, f'网络错误: {exc}', 0
    if page.status_code != 200:
        return False, f'HTTP {page.status_code}', 0
    match = re.search(OG_IMAGE_RE, page.text)
    if not match:
        return False, '无 og:image', 0
    try:
        img = session.get(match.group(1), timeout=timeout)
    except requests.RequestException as exc:
        return False, f'图片网络错误: {exc}', 0
    if img.status_code != 200 or 'image' not in img.headers.get('Content-Type', ''):
        return False, f'图片 HTTP {img.status_code}', 0
    if len(img.content) < MIN_BYTES:
        return False, '文件过小', len(img.content)
    target = dlsite_thumb_path(pid)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    tmp.write_bytes(img.content)
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    tmp.replace(target)
    return True, f'{len(img.content)} bytes', len(img.content)


def run_dlsite(year: int | None = None, month: int | None = None, limit: int = 0,
               interval: float = 1.5, should_stop=None, logger=None) -> dict:
    log = logger or (lambda msg: None)
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = pending_dlsite(conn, year, month, limit)
    finally:
        conn.close()

    session = requests.Session()
    session.headers.update({'User-Agent': DLSITE_UA, 'Referer': 'https://www.dlsite.com/'})

    stats = {
        'status': 'success',
        'mode': 'dlsite',
        'year': year,
        'month': month,
        'interval': interval,
        'total': len(rows),
        'ok': 0,
        'failed': 0,
        'failed_ids': [],
        'started_at': now_ts(),
        'finished_at': None,
        'last_error': '',
    }
    log(f'待下载 {len(rows)} 张 DLsite 封面')
    for i, row in enumerate(rows):
        if should_stop and should_stop():
            stats['status'] = 'stopped'
            break
        pid = str(row['dlsite_id']).strip()
        ok, message, _size = download_dlsite(pid, session)
        if ok:
            stats['ok'] += 1
            log(f'[{i + 1}/{len(rows)}] {pid} ✓ {message} {row["name"]}')
        else:
            stats['failed'] += 1
            stats['failed_ids'].append(pid)
            stats['last_error'] = message
            log(f'[{i + 1}/{len(rows)}] {pid} ✗ {message} {row["name"]}')
        if interval and i + 1 < len(rows):
            time.sleep(interval)

    stats['finished_at'] = now_ts()
    _save_state(stats)
    return stats


def dlsite_coverage() -> dict:
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = conn.execute(
            "SELECT getchu_id, dlsite_id FROM egs_games "
            "WHERE dlsite_id IS NOT NULL AND dlsite_id != ''"
        ).fetchall()
    finally:
        conn.close()
    have = sum(1 for r in rows
               if has_getchu_cover(r) or dlsite_thumb_path(r['dlsite_id']).is_file())
    return {'with_dlsite_id': len(rows), 'with_cover': have, 'missing': len(rows) - have}

