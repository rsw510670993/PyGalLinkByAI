"""Serialized, persistent EGS dashboard jobs. No browser lifetime dependency."""
import argparse
import fcntl
import json
import os
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


def pending_review_count(start_year: int, end_year: int, month: int = 0) -> int:
    """统计指定年月范围内仍需人工审核的记录数。"""
    from .egs_core import open_egs_db, ensure_egs_schema, ensure_review_blacklist_schema
    from .egs_magnet import ensure_egs_magnet_schema

    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        ensure_egs_magnet_schema(conn)
        ensure_review_blacklist_schema(conn)
        row = conn.execute(
            """
            SELECT COUNT(*)
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
            """,
            (int(start_year), int(end_year), int(month), int(month)),
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


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
            with open(log, 'ab', buffering=0) as output:
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
                                       f"请求 {metrics.get('requests', 0)} 次，网络 {metrics.get('network_seconds', 0):.1f}s，等待 {metrics.get('wait_seconds', 0):.1f}s，满分提前结束 {metrics.get('early_stops', 0)} 部")
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
        sql = """
            SELECT * FROM egs_games
             WHERE CAST(substr(date,1,4) AS INTEGER) BETWEEN ? AND ?
               AND link IS NOT NULL AND link != ''
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
        params = [state['start_year'], state['end_year']]
        if state['month']:
            sql += ' AND CAST(substr(date,6,2) AS INTEGER) = ?'
            params.append(state['month'])
        if action in ('check', 'submit'):
            sql += ' AND COALESCE(downloaded,0) = 0'
        if action == 'submit':
            sql += ' AND COALESCE(submitted_115,0) = 0'
        rows = conn.execute(sql + ' ORDER BY date, egs_id', params).fetchall()
        state['total'] = len(rows)
        save()
        year_dirs = {}
        submitted_tasks = {}  # info_hash -> {'egs_id','name','pick_code'}，本轮内同磁链只提交一次
        if action == 'organize':
            from .egs_organize import ensure_folder_schema, organize_single
            ensure_folder_schema(conn)
        for row in rows:
            if should_stop():
                return
            name = row['name']
            state['current'] = name
            save()
            try:
                if action == 'check':
                    from .cli import _check_magnet_exists_with_timeout
                    result, error = _check_magnet_exists_with_timeout(row['link'], 60)
                    if error:
                        raise RuntimeError(error)
                    if result.get('exists'):
                        conn.execute('UPDATE egs_games SET downloaded=1, infohash_hex=?, updated_at=? WHERE egs_id=? AND link=?',
                                     (result.get('infohash_hex'), now_ts(), row['egs_id'], row['link']))
                        conn.commit()
                        report(name, 'success', '校对确认已下载')
                    else:
                        report(name, 'skipped', result.get('message') or '尚未找到已下载内容')
                elif action == 'submit':
                    from .p115_client import offline_submit, _magnet_info_hash
                    from .egs_organize import resolve_cid, mkdir_year_dir
                    info_hash = _magnet_info_hash(row['link'])
                    prior = submitted_tasks.get(info_hash) if info_hash else None
                    if prior is None:
                        year = int(row['date'][:4])
                        directory = f'/GAL/GAL-{year}'
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
                    conn.execute('UPDATE egs_games SET submitted_115=1, submitted_pick_code=?, updated_at=? WHERE egs_id=? AND link=?',
                                 (result.get('pick_code'), now_ts(), row['egs_id'], row['link']))
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
                    outcome = 'failed' if code in ('error', 'conflict', 'ambiguous', 'shared_cid', 'not_dir', 'no_dn_date', 'missing_in_115') else 'skipped' if code in ('no_link', 'not_downloaded', 'in_offline', 'cross_year_confirm', 'month_shift_confirm') else 'success'
                    report(name, outcome, result.get('message') or code, detail=result)
            except Exception as exc:
                report(name, 'failed', str(exc))
            time.sleep(.5)
    finally:
        conn.close()


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
            count = pending_review_count(args.start_year, args.end_year, args.month) if action == 'check' else 0
            print(json.dumps({'status': 'success', 'action': action, 'count': count}, ensure_ascii=False))
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
