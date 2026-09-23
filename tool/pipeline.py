"""Serialized, persistent EGS dashboard jobs. No browser lifetime dependency."""
import argparse
import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .runtime import daily_log_path, now_ts, pid_is_running, read_json, repo_root, runtime_paths, write_json_atomic

ACTIONS = ('crawl', 'magnet', 'check', 'submit', 'organize')
LABELS = dict(zip(ACTIONS, ('获取游戏清单', '获取下载用磁链', '校对115', '提交115', '整理115')))
SUBMISSION_TIMEOUT_SECONDS = 72 * 60 * 60


def _parse_local_timestamp(value):
    if not value:
        return None
    try:
        return time.mktime(time.strptime(str(value), '%Y-%m-%d %H:%M:%S'))
    except (TypeError, ValueError, OverflowError):
        return None


def _submission_started_at(row, check_result, now_epoch=None):
    """Return the persisted submission start, falling back to 115 task time."""
    persisted = _parse_local_timestamp(row['submitted_at'])
    if persisted is not None:
        return persisted
    try:
        task_time = float(check_result.get('offline_task_add_time') or 0)
    except (TypeError, ValueError):
        task_time = 0
    return task_time if task_time > 0 else (now_epoch if now_epoch is not None else time.time())


def _mark_download_failed(conn, row, failed_at, reason):
    """Reuse download_failed while retaining the bad hash as its exclusion key."""
    infohash = str(row['infohash_hex'] or '').strip().lower() or None
    conn.execute(
        """UPDATE egs_games
              SET download_failed=1, download_failed_at=?,
                  downloaded=0, submitted_115=0, submitted_pick_code=NULL,
                  link=NULL, nyaa_name=NULL, infohash_hex=?,
                  torrent_name=NULL, torrent_files=NULL, torrent_size=NULL, resource_kind='',
                  updated_at=?
            WHERE egs_id=?""",
        (failed_at, infohash, now_ts(), row['egs_id']),
    )
    if infohash:
        conn.execute(
            "UPDATE egs_nyaa_candidates SET selected=0 WHERE egs_id=? AND lower(infohash_hex)=?",
            (row['egs_id'], infohash),
        )
    conn.execute(
        """UPDATE egs_nyaa_search_log
              SET selected_infohash=NULL, review_status='none',
                  reviewed_at=?, review_note=?
            WHERE egs_id=?""",
        (failed_at, reason, row['egs_id']),
    )
    if infohash:
        from .egs_core import refresh_magnet_duplicates
        refresh_magnet_duplicates(conn, (infohash,))
    conn.commit()


def paths():
    root = Path(runtime_paths()['status_dir'])
    root.mkdir(parents=True, exist_ok=True)
    return root / 'pipeline.json', root / 'pipeline.lock', root / 'pipeline.stop'


@contextmanager
def locked(blocking=False):
    with paths()[1].open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def status():
    state = read_json(str(paths()[0]), {'running': False})
    if state.get('running') and state.get('pid') and not pid_is_running(int(state['pid'])):
        state.update(running=False, message='任务进程已退出，请查看日志', outcome='error')
    return state


def validate(action, start_year, end_year, month):
    if action not in ACTIONS:
        raise ValueError('未知功能')
    if not 1980 <= start_year <= end_year <= 3000 or not 0 <= month <= 12:
        raise ValueError('年份范围须为 1980–3000，结束年不能小于起始年，月份须为 0–12')


def pending_review_summary(start_year: int, end_year: int, month: int = 0) -> dict:
    """按年份统计指定范围内仍需人工审核的记录。"""
    from .egs_core import open_egs_db, ensure_egs_schema, ensure_review_blacklist_schema
    from .egs_magnet import ensure_egs_magnet_schema

    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        ensure_egs_magnet_schema(conn)
        ensure_review_blacklist_schema(conn)
        rows = conn.execute(
            """
            SELECT CAST(substr(g.date,1,4) AS INTEGER) AS review_year, COUNT(*)
              FROM egs_games g
              JOIN egs_nyaa_search_log l ON l.egs_id = g.egs_id
             WHERE CAST(substr(g.date,1,4) AS INTEGER) BETWEEN ? AND ?
               AND (? = 0 OR CAST(substr(g.date,6,2) AS INTEGER) = ?)
               AND (g.link IS NULL OR g.link = '')
               AND COALESCE(l.result_count, 0) > 0
               AND l.selected_infohash IS NULL
               AND COALESCE(l.review_status, 'pending') = 'pending'
               AND NOT EXISTS (
                   SELECT 1 FROM egs_review_company_blacklist b
                    WHERE b.company IN (g.company, g.egs_company)
               )
             GROUP BY review_year
             ORDER BY review_year
            """,
            (int(start_year), int(end_year), int(month), int(month)),
        ).fetchall()
        years = [{'year': int(row[0]), 'count': int(row[1])} for row in rows]
        return {'count': sum(item['count'] for item in years), 'years': years}
    finally:
        conn.close()


def pending_review_count(start_year: int, end_year: int, month: int = 0) -> int:
    """统计指定年月范围内仍需人工审核的记录数。"""
    return int(pending_review_summary(start_year, end_year, month)['count'])


def start(action, start_year, end_year, month=0, execute=False):
    validate(action, start_year, end_year, month)
    if action == 'check':
        pending = pending_review_count(start_year, end_year, month)
        if pending:
            return {
                'status': 'error',
                'message': f'当前范围还有 {pending} 条待审核记录，请先完成审核再批量校对115',
                'pending_review_count': pending,
            }
    try:
        with locked():
            if status().get('running'):
                return {'status': 'error', 'message': '已有任务运行，请等待完成或停止'}
            # Existing entry points share the database: avoid starting over their workers.
            rp = runtime_paths()
            for key in ('spider_status_path', 'download_status_path', 'check_all_status_path'):
                old = read_json(rp[key], {})
                if old.get('running') and old.get('pid') and pid_is_running(int(old['pid'])):
                    return {'status': 'error', 'message': '旧入口任务仍在运行，请先等待其结束'}
            job_id = uuid.uuid4().hex
            state = dict(running=True, job_id=job_id, action=action, label=LABELS[action],
                         start_year=start_year, end_year=end_year, month=month, execute=execute,
                         started_at=now_ts(), updated_at=now_ts(), done=0, total=0,
                         success=0, failed=0, skipped=0, current='', results=[], message='准备中', outcome='running')
            log = daily_log_path('pipeline')
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            # 网页端(www-data)与命令行(coding)可能交替启动任务；
            # 日志若被另一用户创建且未开放写权限，则删除重建并统一 chmod 666。
            try:
                output = open(log, 'ab', buffering=0)
            except PermissionError:
                os.remove(log)
                output = open(log, 'ab', buffering=0)
            try:
                os.chmod(log, 0o666)
            except OSError:
                pass
            with output:
                worker = subprocess.Popen([sys.executable, '-m', 'tool.pipeline', 'worker', '--job-id', job_id],
                                          cwd=repo_root(), stdout=output, stderr=output, start_new_session=True)
            state['pid'] = worker.pid
            write_json_atomic(str(paths()[0]), state)
            return {'status': 'success', 'message': '任务已启动', 'job_id': job_id}
    except BlockingIOError:
        return {'status': 'error', 'message': '已有任务运行，请等待完成或停止'}


def stop(job_id):
    state = status()
    if not state.get('running') or state.get('job_id') != job_id:
        return {'status': 'error', 'message': '任务已结束或已切换，请刷新状态'}
    write_json_atomic(str(paths()[2]), {'job_id': job_id})
    # Most requests return promptly and are stopped cooperatively between items.
    # A p115client call can, however, remain inside its automatic relogin loop
    # indefinitely. Only terminate the exact current worker after its persisted
    # progress has been stale for three minutes; reruns are idempotent.
    updated_at = int(state.get('updated_at') or 0)
    pid = int(state.get('pid') or 0)
    if updated_at and now_ts() - updated_at >= 180 and pid and pid_is_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            return {'status': 'success', 'message': '任务已超过3分钟无进度，已终止卡住的请求'}
        except ProcessLookupError:
            pass
    return {'status': 'success', 'message': '已请求停止，当前请求结束后停止'}


def backup(job_id):
    from .egs_core import default_egs_db_path
    target = Path(repo_root()) / 'db_backups' / f'egs.before_pipeline_{job_id}.db'
    target.parent.mkdir(exist_ok=True)
    with sqlite3.connect(default_egs_db_path()) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return str(target)


def execute_job(state, save, should_stop):
    from .egs_core import open_egs_db, ensure_egs_schema, fetch_egs_month, upsert_egs_rows
    action = state['action']
    years = range(state['start_year'], state['end_year'] + 1)
    months = [state['month']] if state['month'] else range(1, 13)
    conn = open_egs_db()
    ensure_egs_schema(conn)

    def report(name, outcome, message='', **details):
        state['done'] += 1
        state[outcome] += 1
        state['results'].append(dict(name=name, outcome=outcome, message=message, **details))
        save()

    try:
        if action in ('crawl', 'magnet'):
            if action == 'magnet':
                from .egs_magnet import RequestPacer, run_magnet
                pacer = RequestPacer(should_stop)
            state['total'] = len(years) * len(months)
            save()
            for year in years:
                for month in months:
                    if should_stop():
                        return
                    name = f'{year}-{month:02d}'
                    state['current'] = name
                    save()
                    try:
                        if action == 'crawl':
                            rows = fetch_egs_month(year, month)
                            result = upsert_egs_rows(conn, rows)
                            message = f"获取 {len(rows)}，新增 {result['inserted']}，更新 {result['updated']}，未变 {result['unchanged']}"
                            report(name, 'success', message)
                        else:
                            def progress(current, done, total):
                                state['current'] = f'{name} · {current} ({done}/{total})'
                                save()
                            result = run_magnet(year, month=month, should_stop=should_stop, progress=progress, pacer=pacer)
                            metrics = result.get('metrics', {})
                            summary = (f"待搜索 {result['total']}，匹配 {result['selected']}，无结果 {result['no_result']}，低分 {result['low_score']}，错误 {result['error']}；"
                                       f"跳过：已有磁链 {result.get('skip_linked', 0)}，搜索历史 {result.get('skip_history', 0)}，未发售 {result.get('skip_unreleased', 0)}；"
                                       f"请求 {metrics.get('requests', 0)} 次，网络 {metrics.get('network_seconds', 0):.1f}s，等待 {metrics.get('wait_seconds', 0):.1f}s，满分提前结束 {metrics.get('early_stops', 0)} 部；"
                                       f"末段间隔 {result.get('final_interval', 0):.2f}s，429退避 {metrics.get('backoffs', 0)} 次，提前结束省 {metrics.get('queries_saved', 0)} 次查询")
                            if result.get('stopped'):
                                state['results'].append(dict(name=name, outcome='skipped', message='已停止（本月未完成）；' + summary, detail=result))
                                save()
                                return
                            report(name, 'failed' if result['error'] else 'success', summary, detail=result)
                            if result.get('timeout_aborted'):
                                return

                    except sqlite3.DatabaseError:
                        # Shared storage failures affect all remaining games/months.
                        raise
                    except Exception as exc:
                        report(name, 'failed', str(exc))
                    if action == 'crawl':
                        time.sleep(1)
            return

        from .p115_client import get_login_status
        from .egs_magnet import ensure_egs_magnet_schema
        from .egs_core import ensure_review_blacklist_schema
        login = get_login_status()
        if not login.get('logged_in'):
            raise RuntimeError('115 未登录，请到 EGS 数据页登录后重试')
        ensure_egs_magnet_schema(conn)
        ensure_review_blacklist_schema(conn)
        if state['month']:
            scope_dates = [
                f"{year:04d}-{int(state['month']):02d}"
                for year in range(int(state['start_year']), int(state['end_year']) + 1)
            ]
            date_filter = "date IN (" + ",".join("?" for _ in scope_dates) + ")"
            params = scope_dates
        else:
            date_filter = "date >= ? AND date < ?"
            params = [
                f"{int(state['start_year']):04d}-01",
                f"{int(state['end_year']) + 1:04d}-01",
            ]
        sql = f"""
            SELECT * FROM egs_games
             WHERE {date_filter}
               AND link IS NOT NULL AND link != ''
               AND magnet_duplicate = 0
               AND submission_excluded = 0
               AND NOT EXISTS (
                   SELECT 1 FROM egs_review_company_blacklist b
                    WHERE b.company IN (egs_games.company, egs_games.egs_company)
               )
               AND NOT EXISTS (
                   SELECT 1 FROM egs_nyaa_search_log l
                    WHERE l.egs_id = egs_games.egs_id
                      AND COALESCE(l.result_count, 0) > 0
                      AND l.selected_infohash IS NULL
                      AND COALESCE(l.review_status, 'pending') = 'pending'
               )
        """
        if action in ('check', 'submit'):
            sql += ' AND downloaded = 0'
        if action == 'submit':
            sql += ' AND submitted_115 = 0'
        rows = conn.execute(sql + ' ORDER BY date, egs_id', params).fetchall()
        state['total'] = len(rows)
        save()
        year_dirs = {}
        check_year_items = {}
        submitted_tasks = {}  # info_hash -> {'egs_id','name','pick_code'}，本轮内同磁链只提交一次
        check_offline_tasks = None
        if action == 'check' and rows:
            from .p115_client import offline_list
            offline = offline_list()
            if not offline.get('success'):
                raise RuntimeError(offline.get('message') or '无法读取115离线任务列表')
            check_offline_tasks = offline.get('tasks') or []
        if action == 'organize':
            from .egs_organize import ensure_folder_schema, organize_single, organize_report_outcome, record_organize_issue
            ensure_folder_schema(conn)
        for row in rows:
            if should_stop():
                return
            name = row['name']
            state['current'] = name
            save()
            try:
                if action == 'check':
                    from .egs_organize import (
                        adopt_downloaded_folder_by_infohash,
                        adopt_downloaded_folder_by_torrent_name,
                        list_dir_children, resolve_cid, resolve_dn_timestamp,
                    )
                    from .cli import _check_magnet_exists_with_timeout
                    lookup_years = [str(row['date'])[:4]]
                    if row['resource_kind'] == 'collection_dlc':
                        resource_date, _ = resolve_dn_timestamp(
                            row['link'], row['release_ts'],
                            torrent_name=row['torrent_name'],
                        )
                        resource_year = str(resource_date or '')[:4]
                        if resource_year and resource_year not in lookup_years:
                            lookup_years.append(resource_year)
                    reused = None
                    for lookup_year in lookup_years:
                        year_path = f"/GAL/GAL-{lookup_year}"
                        if year_path not in year_dirs:
                            year_dirs[year_path] = resolve_cid(year_path)
                        if year_path not in check_year_items:
                            year_cid = year_dirs[year_path]
                            check_year_items[year_path] = (
                                list_dir_children(year_cid) if year_cid else []
                            )
                        reused = adopt_downloaded_folder_by_torrent_name(
                            conn, row['date'], name, row['link'], row['infohash_hex'],
                            row['torrent_name'], company=row['company'],
                            year_dir_cid=year_dirs[year_path],
                            year_items=check_year_items[year_path],
                            folder_year=lookup_year,
                        )
                        if reused:
                            break
                    if reused:
                        report(name, 'success', '按种子 info.name 精确匹配历史下载目录')
                        continue
                    reused = adopt_downloaded_folder_by_infohash(
                        conn, row['date'], name, row['link'], row['infohash_hex'],
                        allowed_parent_paths=[f"/GAL/GAL-{year}" for year in lookup_years],
                    )
                    if reused:
                        report(
                            name, 'success',
                            f"相同磁链，复用已下载目录（来源: {reused['source']}）",
                        )
                        continue
                    result, error = _check_magnet_exists_with_timeout(
                        row['link'], 60, strict_infohash=True,
                        offline_tasks=check_offline_tasks,
                        allowed_save_paths=[f"/GAL/GAL-{year}" for year in lookup_years],
                    )
                    if error:
                        raise RuntimeError(error)
                    if result.get('download_failed'):
                        # 115 离线任务明确失败：回滚到无磁链状态，供后续重新爬取其他磁链
                        failed_at = time.strftime('%Y-%m-%d %H:%M:%S')
                        _mark_download_failed(conn, row, failed_at, '115离线任务明确失败')
                        report(name, 'failed', '115下载任务失败，已回滚为无磁链；可重新爬取其他磁链')
                    elif result.get('in_offline_tasks') and not result.get('offline_finished'):
                        now_epoch = time.time()
                        started = _submission_started_at(row, result, now_epoch)
                        if not row['submitted_at']:
                            submitted_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))
                            conn.execute(
                                'UPDATE egs_games SET submitted_at=? WHERE egs_id=?',
                                (submitted_at, row['egs_id']),
                            )
                            conn.commit()
                        if now_epoch - started >= SUBMISSION_TIMEOUT_SECONDS:
                            failed_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now_epoch))
                            _mark_download_failed(
                                conn, row, failed_at,
                                '115离线任务提交超过72小时仍未完成，磁链已标记不可下载',
                            )
                            report(name, 'failed', '115下载超过72小时未完成，已标记磁链不可用并回滚')
                        else:
                            report(name, 'skipped', result.get('message') or '磁链在115离线任务中，等待下载完成')
                    elif result.get('exists'):
                        conn.execute(
                            '''UPDATE egs_games
                                  SET downloaded=1, submitted_115=1, download_failed=0,
                                      infohash_hex=?, updated_at=?
                                WHERE egs_id=? AND link=?''',
                            (result.get('infohash_hex'), now_ts(), row['egs_id'], row['link']))
                        conn.commit()
                        report(name, 'success', '校对确认已下载')
                    else:
                        report(name, 'skipped', result.get('message') or '尚未找到已下载内容')
                elif action == 'submit':
                    from .p115_client import offline_submit, _magnet_info_hash
                    from .egs_organize import (
                        adopt_downloaded_folder_by_infohash, resolve_cid, mkdir_year_dir,
                    )
                    info_hash = _magnet_info_hash(row['link'])
                    year = int(row['date'][:4])
                    directory = f'/GAL/GAL-{year}'
                    # 提交阶段也只复用目标 /GAL 年份目录中的同 hash 目录；
                    # /GAL.old 或其它位置的旧目录留给最后人工合并。
                    reused = adopt_downloaded_folder_by_infohash(
                        conn, row['date'], name, row['link'], row['infohash_hex'],
                        allowed_parent_paths=[directory],
                    )
                    if reused:
                        report(
                            name, 'success',
                            f"相同磁链，复用已下载目录（来源: {reused['source']}）",
                        )
                        if info_hash:
                            submitted_tasks[info_hash] = {
                                'egs_id': row['egs_id'], 'name': name,
                                'pick_code': reused.get('pick_code') or '',
                            }
                        continue
                    prior = submitted_tasks.get(info_hash) if info_hash else None
                    if prior is None:
                        if directory not in year_dirs:
                            cid = resolve_cid(directory)
                            if not cid:
                                cid = mkdir_year_dir(year)
                            if not cid:
                                raise RuntimeError('无法确定提交目录')
                            year_dirs[directory] = cid
                        result = offline_submit(row['link'], directory)
                        if not result.get('success'):
                            raise RuntimeError(result.get('message') or '提交失败')
                        if info_hash:
                            submitted_tasks[info_hash] = {'egs_id': row['egs_id'], 'name': name, 'pick_code': result.get('pick_code') or ''}
                    else:
                        # 同一条磁链本轮已提交过（本篇/补丁等重复条目），直接落库不再请求 115。
                        result = {'pick_code': prior['pick_code'], 'duplicate': True}
                    submitted_at = time.strftime('%Y-%m-%d %H:%M:%S')
                    conn.execute('UPDATE egs_games SET submitted_115=1, submitted_pick_code=?, submitted_at=?, updated_at=? WHERE egs_id=? AND link=?',
                                 (result.get('pick_code'), submitted_at, now_ts(), row['egs_id'], row['link']))
                    conn.commit()
                    if prior is not None:
                        report(name, 'success', f"磁链与《{prior['name']}》重复，沿用已提交任务")
                    elif result.get('duplicate'):
                        report(name, 'success', result.get('message') or '115 已存在相同任务，视为提交成功')
                    else:
                        report(name, 'success', '已提交115')
                else:
                    result = organize_single(row['date'], name, dry_run=not state['execute'], conn=conn, year_dirs=year_dirs)
                    code = result.get('status')
                    outcome = organize_report_outcome(code)
                    record_organize_issue(conn, date=row['date'], name=name, code=code,
                                          executed=bool(state['execute']),
                                          detail=result, job_id=state.get('job_id'), egs_id=row['egs_id'])
                    report(name, outcome, result.get('message') or code, detail=result)
            except Exception as exc:
                report(name, 'failed', str(exc))
            time.sleep(.5)
    finally:
        conn.close()


def cleanup_success_backup(backup_path):
    """Delete only this pipeline's generated DB backup after a complete run."""
    if not backup_path:
        return False
    target = Path(backup_path).resolve()
    backup_dir = (Path(repo_root()) / 'db_backups').resolve()
    if (target.parent != backup_dir
            or not target.name.startswith('egs.before_pipeline_')
            or target.suffix != '.db'):
        return False
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    return True


def worker(job_id):
    # The launcher holds the lock until the initial state including PID is written.
    with locked(blocking=True):
        state = read_json(str(paths()[0]), {})
        if state.get('job_id') != job_id:
            return
        def save():
            state['updated_at'] = now_ts()
            write_json_atomic(str(paths()[0]), state)
        def should_stop():
            return read_json(str(paths()[2]), {}).get('job_id') == job_id
        try:
            state['backup'] = backup(job_id)
            save()
            execute_job(state, save, should_stop)
            state['outcome'] = 'stopped' if should_stop() else 'partial' if state['failed'] else 'complete'
            state['message'] = {'stopped': '已停止', 'partial': '已完成，部分项目需处理', 'complete': '已完成'}[state['outcome']]
            if state['outcome'] == 'complete' and cleanup_success_backup(state.get('backup')):
                state['backup'] = None
                state['backup_cleaned'] = True
        except Exception as exc:
            state['outcome'] = 'error'
            state['message'] = str(exc)
            import traceback
            traceback.print_exc()
        finally:
            state['running'] = False
            state['current'] = ''
            save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('start', 'status', 'stop', 'worker', 'preflight'))
    parser.add_argument('--action', choices=ACTIONS)
    parser.add_argument('--start-year', type=int)
    parser.add_argument('--end-year', type=int)
    parser.add_argument('--month', type=int, default=0)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--job-id')
    args = parser.parse_args()
    try:
        if args.command == 'worker':
            worker(args.job_id)
            return
        if args.command == 'preflight':
            action = args.action or 'check'
            validate(action, args.start_year or 1980, args.end_year or 3000, args.month or 0)
            summary = pending_review_summary(args.start_year, args.end_year, args.month) if action == 'check' else {'count': 0, 'years': []}
            review_year = summary['years'][0]['year'] if summary['years'] else None
            print(json.dumps({
                'status': 'success',
                'action': action,
                'count': summary['count'],
                'review_year': review_year,
                'review_years': summary['years'],
            }, ensure_ascii=False))
            return
        if args.command == 'start':
            result = start(args.action, args.start_year, args.end_year, args.month, args.execute)
        elif args.command == 'stop':
            result = stop(args.job_id)
        else:
            result = status()
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
