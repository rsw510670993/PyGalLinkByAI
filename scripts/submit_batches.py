#!/usr/bin/env python3
"""分批提交 driver（可续跑，非定时任务）。

2026-11-26：审核 gate 已移除（原 pending_review_count 与 preflight 两道门禁作废），
driver 不再跑预检，直接分批提交、轮询到结束，再进入下一批。

用法（写库，必须 www-data）：
  cd /var/www/html/gshelf
  runuser -u www-data -- .venv/bin/python scripts/submit_batches.py --dry-run   # 只检查+打印计划
  runuser -u www-data -- .venv/bin/python scripts/submit_batches.py             # 真跑
  runuser -u www-data -- .venv/bin/python scripts/submit_batches.py --resume    # 断点续跑
  runuser -u www-data -- .venv/bin/python scripts/submit_batches.py --only 3    # 只跑第 3 批

批次顺序（先老后新，风险大的先验证）：
  1) 2005-2007   2) 2008-2009   3) 2010-2011   4) 2012-2026
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / '.venv' / 'bin' / 'python')
STATE = ROOT / 'status' / 'submit_batches_state.json'
LOG = ROOT / 'status' / 'submit_batches.log'

BATCHES = [
    (1, 2005, 2007, '2005-2007 老作补链'),
    (2, 2008, 2009, '2008-2009 主体'),
    (3, 2010, 2011, '2010-2011（含曾秒退的 2011）'),
    (4, 2012, 2026, '2012 及以后'),
]

POLL_SECONDS = 15
# 批次 3（2010-2011，约 1,148 条）按历史吞吐可能接近 3 小时，留一倍余量。
BATCH_TIMEOUT = 6 * 3600


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as fh:
        fh.write(line + '\n')


def run_cli(args: list[str]) -> dict:
    proc = subprocess.run([PY, *args], cwd=str(ROOT), capture_output=True, text=True)
    out = (proc.stdout or '').strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {'status': 'error', 'message': (proc.stderr or proc.stdout or '')[-300:]}


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            pass
    return {'batches': {}}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(STATE)


def pipeline_status() -> dict:
    path = ROOT / 'status' / 'pipeline.json'
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True


def wait_finish(job_id: str, timeout: int = BATCH_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = pipeline_status()
        if cur.get('job_id') == job_id and cur.get('running'):
            if not _pid_alive(cur.get('pid')):
                return {**cur, 'running': False,
                        'outcome': cur.get('outcome') or 'error',
                        'message': 'worker 进程已退出'}
        if cur.get('job_id') == job_id and not cur.get('running'):
            return cur
        time.sleep(POLL_SECONDS)
    return {'outcome': 'timeout', 'job_id': job_id}


def run_batch(no: int, start_year: int, end_year: int, label: str, state: dict, dry_run: bool) -> bool:
    key = str(no)
    rec = state['batches'].get(key, {})
    if rec.get('outcome') == 'complete':
        log(f'[批次 {no}] {label} 已完成，跳过（--resume 时保持）')
        return True

    log(f'[批次 {no}] {label} 开始提交 {start_year}-{end_year}')
    if dry_run:
        log(f'[批次 {no}] dry-run：跳过实际提交')
        return True

    started = run_cli(['-m', 'tool.pipeline', 'start', '--action', 'submit',
                       '--start-year', str(start_year), '--end-year', str(end_year),
                       '--month', '0', '--execute'])
    if started.get('status') != 'success':
        log(f'[批次 {no}] 启动失败：{started.get("message")}')
        rec.update({'outcome': 'start_failed', 'message': started.get('message')})
        state['batches'][key] = rec
        save_state(state)
        return False

    job_id = started.get('job_id')
    log(f'[批次 {no}] 已启动 job={job_id}，等待完成…')
    final = wait_finish(job_id)
    outcome = final.get('outcome') or ('complete' if not final.get('failed') else 'partial')
    log(f'[批次 {no}] 结束：outcome={outcome} total={final.get("total")} '
        f'success={final.get("success")} failed={final.get("failed")} skipped={final.get("skipped")}')
    rec.update({'outcome': outcome, 'job_id': job_id,
                'total': final.get('total'), 'success': final.get('success'),
                'failed': final.get('failed'), 'skipped': final.get('skipped'),
                'finished_at': time.strftime('%Y-%m-%d %H:%M:%S')})
    state['batches'][key] = rec
    save_state(state)
    return outcome == 'complete'


def cooldown(args) -> None:
    """批次间冷却：固定等待 +（可选）等 115 离线队列变浅。

    冷却只是放慢节奏：查询失败或等待超时都告警后继续，不中断后续批次。
    """
    if args.pause_seconds > 0:
        log(f'批次间冷却：等待 {args.pause_seconds}s 再进入下一批')
        time.sleep(args.pause_seconds)
    if args.wait_queue <= 0:
        return
    try:
        from tool.p115_client import offline_list
    except ImportError:
        # 直接跑 `python scripts/submit_batches.py` 时 sys.path[0] 是 scripts/，
        # 仓库根不在导入路径上；补一次，再失败才放弃等待。
        sys.path.insert(0, str(ROOT))
        try:
            from tool.p115_client import offline_list
        except ImportError as exc:
            log(f'警告：无法导入 tool.p115_client（{exc}），跳过队列等待')
            return
    deadline = time.time() + 60 * 60
    while True:
        try:
            listing = offline_list()
        except Exception as exc:
            log(f'警告：读取 115 离线列表失败（{exc}），跳过队列等待')
            return
        if not listing.get('success'):
            log(f'警告：读取 115 离线列表失败（{listing.get("message")}），跳过队列等待')
            return
        waiting = sum(1 for task in (listing.get('tasks') or [])
                      if task.get('display_status') == 'waiting')
        if waiting <= args.wait_queue:
            log(f'批次间冷却：115 等待中任务 {waiting} 条，已降到 {args.wait_queue} 以内，继续下一批')
            return
        if time.time() >= deadline:
            log(f'批次间冷却：等待超过 60 分钟（仍有 {waiting} 条 waiting），继续下一批')
            return
        log(f'批次间冷却：115 等待中任务 {waiting} 条，60s 后复查')
        time.sleep(60)


def run_check(start_year: int, end_year: int, delay: int) -> None:
    """批次后补一次 check：把「提交后 15 分钟校对」从浏览器搬进无人流程。"""
    if delay > 0:
        log(f'等待 {delay}s 后校对 {start_year}-{end_year}')
        time.sleep(delay)
    started = run_cli(['-m', 'tool.pipeline', 'start', '--action', 'check',
                       '--start-year', str(start_year), '--end-year', str(end_year),
                       '--month', '0', '--execute'])
    if started.get('status') != 'success':
        log(f'校对启动失败：{started.get("message")}')
        return
    final = wait_finish(started.get('job_id'))
    log(f'校对结束：outcome={final.get("outcome")} total={final.get("total")} '
        f'success={final.get("success")} failed={final.get("failed")} skipped={final.get("skipped")}')


def main() -> int:
    ap = argparse.ArgumentParser(description='12-01 分批提交 driver（可续跑）')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划，不实际提交')
    ap.add_argument('--resume', action='store_true', help='跳过已完成批次')
    ap.add_argument('--only', type=int, help='只跑指定批次号')
    ap.add_argument('--pause-seconds', type=int, default=0,
                    help='每批结束后固定等待秒数再进入下一批（默认 0，不等待）')
    ap.add_argument('--wait-queue', type=int, default=0,
                    help='每批结束后等 115 离线 waiting 任务数降到该值以内（最长 60 分钟，默认 0 不等）')
    ap.add_argument('--auto-check', action='store_true',
                    help='每批提交结束后等待 --check-delay 秒再跑一次 check（默认关闭）')
    ap.add_argument('--check-delay', type=int, default=900,
                    help='--auto-check 的等待秒数（默认 900，与「提交后 15 分钟校对」一致）')
    args = ap.parse_args()

    if subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip() != 'www-data':
        log('警告：当前不是 www-data，写库可能造成 -wal 属主异常；建议 runuser -u www-data 运行')

    state = load_state() if args.resume else {'batches': {}}
    ok = True
    for no, sy, ey, label in BATCHES:
        if args.only and no != args.only:
            continue
        ok = run_batch(no, sy, ey, label, state, args.dry_run) and ok
        if not ok:
            log('批次中断，后续批次未执行；修复后可用 --resume 继续')
            break
        if not args.dry_run:
            cooldown(args)
            if args.auto_check:
                run_check(sy, ey, args.check_delay)
    log('全部批次结束' if ok else '存在未完成批次')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
