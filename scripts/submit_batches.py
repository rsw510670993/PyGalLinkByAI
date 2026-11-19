#!/usr/bin/env python3
"""12-01 分批提交 driver（可续跑，非定时任务）。

背景：submit/check 有两道独立审核 gate（pipeline.start 的 pending_review_count
与 rows 查询里的 pending 过滤），跳过 preflight 会出现「启动了但 0 条提交」。
本 driver 每批提交前强制 preflight，提交后轮询到结束，再进入下一批。

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
BATCH_TIMEOUT = 3 * 3600


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


def preflight(start_year: int, end_year: int) -> dict:
    return run_cli(['-m', 'tool.pipeline', 'preflight', '--action', 'submit',
                    '--start-year', str(start_year), '--end-year', str(end_year), '--month', '0'])


def wait_finish(job_id: str, timeout: int = BATCH_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = pipeline_status()
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

    pre = preflight(start_year, end_year)
    pending = int(pre.get('count') or 0)
    if pending:
        log(f'[批次 {no}] {label} 被审核 gate 挡住：还有 {pending} 条低分待审，先复核再跑')
        rec.update({'outcome': 'blocked', 'pending_review': pending})
        state['batches'][key] = rec
        save_state(state)
        return False

    log(f'[批次 {no}] {label} preflight 通过（待审 0），提交 {start_year}-{end_year}')
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


def main() -> int:
    ap = argparse.ArgumentParser(description='12-01 分批提交 driver（可续跑）')
    ap.add_argument('--dry-run', action='store_true', help='只跑 preflight 并打印计划')
    ap.add_argument('--resume', action='store_true', help='跳过已完成批次')
    ap.add_argument('--only', type=int, help='只跑指定批次号')
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
    log('全部批次结束' if ok else '存在未完成批次')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
