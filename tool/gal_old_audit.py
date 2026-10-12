"""只读审计 /GAL.old/GAL-YYYY 与 /GAL/GAL-YYYY 的关系，产出报告，不做任何变更。

设计约束（来自既有教训，见 docs/EGS_115_ORGANIZATION_PLAYBOOK.md 与记忆）：
  * **全程只读**：不删除、不移动、不改数据库；
  * 对照目录只认可核实的定位——目录名里的 RJ 号反查 `egs_games`→`egs_115_folders`
    精确映射，或 EGS 名称归一化后**完全相等**；**不做模糊匹配**（粗匹配曾把
    アマナツ＋ 错配到原作的 2022 目录），核实不了的标 `unmatched` 交人工判断；
  * 判定用递归 walk 出来的 (路径, SHA1) 集合，115 只能取到这一层可信信息。

用法：
    python -m tool.gal_old_audit --year 2020            # 出报告（默认写清单到 db_backups/）
    python -m tool.gal_old_audit --year 2020 --no-manifest
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import time
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GAL_OLD_ROOT = '/GAL.old'
GAL_ROOT = '/GAL'
# 宣传件：判定“旧库独有实质文件”时应剔除，避免把广告当成内容差异。
NOISE_MARKERS = ('girlcelly@', 'h-game uploaded', 'nemuandharuka.png', '.url')


def _core(text: str) -> str:
    """归一化作品名用于**精确**比较：去方/圆括号段、去空白与标点、NFKC、小写。"""
    s = unicodedata.normalize('NFKC', text or '')
    s = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', s)
    s = re.sub(r'\s*[+＋].*$', '', s)
    return re.sub(r'[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]', '', s.lower())


def is_noise(path: str) -> bool:
    low = (path or '').lower()
    return any(marker in low for marker in NOISE_MARKERS)


def _children(cid, max_pages=90):
    from .egs_organize import list_dir_children
    return list_dir_children(cid, max_pages=max_pages) or []


def walk_files(cid, max_pages=90):
    """递归列出目录下所有文件 → [{path, sha1}]（sha1 需要底层原始字段，单独取）。"""
    from .p115_client import _import_p115client, load_client

    _, check_response = _import_p115client()
    client = load_client()
    if client is None:
        raise RuntimeError('115 未登录')

    def children(node_cid):
        out, offset = [], 0
        while True:
            resp = check_response(client.fs_files({'cid': str(node_cid), 'limit': 200,
                                                   'offset': offset, 'show_dir': 1}))
            data = resp.get('data') or []
            out.extend(data)
            if len(data) < 200:
                break
            offset += len(data)
        return out

    acc = []

    def rec(node_cid, prefix, depth):
        if depth > 8:
            return
        for it in children(node_cid):
            path = prefix + it['n']
            if it.get('fid'):
                acc.append({'path': path, 'sha1': it.get('sha')})
            else:
                rec(it.get('cid'), path + '/', depth + 1)

    rec(cid, '', 0)
    return acc


def resolve(path):
    from .egs_organize import resolve_cid
    try:
        return resolve_cid(path)
    except Exception:
        return None


def find_counterpart(conn, year: int, old_name: str):
    """为旧库目录找一个**可核实**的 /GAL 对照目录；核不出来返回 None。"""
    # ① RJ 号 → egs_games → egs_115_folders 精确映射
    match = re.search(r'(RJ\d{6,8})', old_name or '', re.I)
    if match:
        row = conn.execute(
            'SELECT egs_id, date, name FROM egs_games WHERE dlsite_id LIKE ? LIMIT 1',
            (f'%{match.group(1)}%',),
        ).fetchone()
        if row:
            mapped = conn.execute(
                'SELECT folder_name, cid, folder_path FROM egs_115_folders WHERE date=? AND name=?',
                (row['date'], row['name']),
            ).fetchone()
            if mapped and mapped['cid']:
                return {'egs_id': row['egs_id'], 'record': row['name'], 'how': 'rj_id',
                        'folder_name': mapped['folder_name'], 'cid': str(mapped['cid']),
                        'folder_path': mapped['folder_path']}

    # ② 名称归一化后完全相等（仅同年记录），不做模糊匹配
    target = _core(old_name)
    if not target:
        return None
    for row in conn.execute(
        "SELECT g.egs_id, g.date, g.name, f.folder_name, f.cid, f.folder_path"
        ' FROM egs_games g JOIN egs_115_folders f ON f.date = g.date AND f.name = g.name'
        " WHERE g.date LIKE ? AND f.cid IS NOT NULL", (f'{year}-%',)
    ):
        if _core(row['name']) == target or _core(row['folder_name']) == target:
            return {'egs_id': row['egs_id'], 'record': row['name'], 'how': 'name_exact',
                    'folder_name': row['folder_name'], 'cid': str(row['cid']),
                    'folder_path': row['folder_path']}
    return None


def verdict(old_set, new_set):
    """旧库目录相对 /GAL 目录的包含关系。"""
    if not old_set:
        return 'old_empty'
    if old_set <= new_set:
        return 'old_subset_new'          # 旧库没有 /GAL 之外的东西 → 可视为冗余
    if old_set & new_set:
        return 'has_common'
    return 'no_overlap'


def _save_state(path: Path, state: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(path)
    except OSError:
        pass


class ItemTimeout(Exception):
    """单项 115 调用超时（不放弃整轮）。"""


def _item_alarm(_signum, _frame):
    raise ItemTimeout()


def _analyze_entry(conn, year: int, entry: dict, max_pages: int,
                   per_item_timeout: int, deadline: float | None) -> dict:
    """单条旧库目录的结论；单项超时只丢这一条，不拖垮整轮。"""
    import signal

    item = {'name': entry['n'], 'counterpart': None, 'verdict': 'unmatched',
            'old_files': 0, 'new_files': 0, 'old_only': [], 'old_only_count': 0}
    counterpart = find_counterpart(conn, year, entry['n'])
    if not counterpart:
        return item
    item['counterpart'] = counterpart
    if not entry.get('is_dir'):
        # 旧库这一条是单文件（多为整包 rar/zip），没有可比对的目录内容
        item['verdict'] = 'single_file'
        return item
    limit = int(per_item_timeout)
    if deadline:
        left = int(deadline - time.time())
        if left <= 0:
            item['verdict'] = 'timeout'
            return item
        limit = max(1, min(limit, left))
    armed = hasattr(signal, 'SIGALRM')
    if armed:
        signal.signal(signal.SIGALRM, _item_alarm)
        signal.alarm(limit)
    try:
        old_set = {(f['path'], f['sha1']) for f in walk_files(entry['cid'], max_pages=max_pages)}
        new_set = {(f['path'], f['sha1'])
                   for f in walk_files(counterpart['cid'], max_pages=max_pages)}
    except ItemTimeout:
        item['verdict'] = 'timeout'
        return item
    finally:
        if armed:
            signal.alarm(0)
    item['verdict'] = verdict(old_set, new_set)
    item['old_files'] = len(old_set)
    item['new_files'] = len(new_set)
    substance = [path for path, _ in sorted(old_set - new_set) if not is_noise(path)]
    item['old_only'] = substance[:30]
    item['old_only_count'] = len(substance)
    return item


def audit_old_year(year: int, write_manifest: bool = True, max_pages: int = 90,
                   state_path=None, progress=None, per_item_timeout: int = 180,
                   deadline: float | None = None) -> dict:
    """审计单个年份：旧库条目 × /GAL 对照目录 × (路径,SHA1) 关系。只读。

    可续跑：每条结论落盘到 `status/gal_old_audit_<year>.json`，重跑直接复用；
    到 `deadline` 或单项超时都不会丢掉已完成的部分，结果里用 `partial` 标出剩余量。
    """
    from .egs_core import open_egs_db

    started = datetime.datetime.now()
    result = {
        'action': 'old_audit', 'year': int(year), 'ok': True, 'status': 'complete',
        'old_dir': f'{GAL_OLD_ROOT}/GAL-{year}', 'old_dir_exists': False,
        'counts': {'total': 0, 'old_subset_new': 0, 'has_common': 0, 'no_overlap': 0,
                   'unmatched': 0, 'old_empty': 0, 'single_file': 0, 'timeout': 0},
        'items': [], 'artifacts': [], 'highlights': [], 'error': None,
    }

    old_cid = resolve(result['old_dir'])
    if not old_cid:
        result.update(status='no_old_dir', ok=True,
                      error=None,
                      highlights=[f'{result["old_dir"]} 不存在或无法解析（该年度旧库已清空）'])
        return result
    result['old_dir_exists'] = True

    old_entries = _children(old_cid, max_pages=max_pages)
    result['counts']['total'] = len(old_entries)
    if not old_entries:
        result['highlights'].append('旧库目录为空（0 个子项）')
        return result

    # 逐年可续跑：每条结论落盘，中断/超时后下次直接复用，不重复烧 115 API。
    state_path = Path(state_path or (REPO_ROOT / 'status' / f'gal_old_audit_{year}.json'))
    state = {'year': year, 'items': {}}
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text(encoding='utf-8'))
            if loaded.get('year') == year:
                state = loaded
        except (OSError, ValueError):
            pass
    cached = state.get('items') or {}

    conn = open_egs_db()
    done = 0
    try:
        for entry in old_entries:
            name = entry['n']
            if name in cached:
                item = cached[name]
            else:
                if deadline and time.time() > deadline:
                    result['partial'] = len(old_entries) - done
                    result['highlights'].append(
                        f'本次到时间上限，剩 {result["partial"]} 条未审（已落盘，下次继续）')
                    break
                item = _analyze_entry(conn, year, entry, max_pages,
                                      per_item_timeout, deadline)
                cached[name] = item
                state['items'] = cached
                _save_state(state_path, state)
            result['items'].append(item)
            result['counts'][item['verdict']] = result['counts'].get(item['verdict'], 0) + 1
            done += 1
            if progress:
                try:
                    progress(done, len(old_entries), name)
                except Exception:                      # 进度回调绝不能打断审计
                    pass
    finally:
        conn.close()

    counts = result['counts']
    result['highlights'] = [
        f"旧库 {counts['total']} 条（本次审 {done}）：旧⊆新 {counts.get('old_subset_new', 0)}、"
        f"有交集 {counts.get('has_common', 0)}、无交集 {counts.get('no_overlap', 0)}、"
        f"单文件 {counts.get('single_file', 0)}、超时 {counts.get('timeout', 0)}、"
        f"核实不到 {counts.get('unmatched', 0)}",
        '旧⊆新 仅表示“旧库没有 /GAL 之外的东西”，可作冗余候选；**删除前仍需人工确认**。',
    ]
    if result.get('partial'):
        result['status'] = 'partial'
    try:
        result['state_file'] = str(state_path.relative_to(REPO_ROOT))
    except ValueError:                             # 测试或自定义路径可能不在仓库内
        result['state_file'] = str(state_path)
    result['finished_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    result['duration_s'] = int((datetime.datetime.now() - started).total_seconds())

    if write_manifest:
        out_dir = REPO_ROOT / 'db_backups'
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"gal_old_{year}_audit_{datetime.datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        result['artifacts'].append(str(path.relative_to(REPO_ROOT)))
    return result


def clean_old_year(year: int, execute: bool = False, progress=None,
                   per_item_timeout: int = 180, deadline: float | None = None,
                   state_path=None, write_manifest: bool = True) -> dict:
    """清理旧库**冗余候选**：只处理审计结论为 `old_subset_new` 的条目。

    铁律（沿用 0mv2fl7g 的执行顺序）：① 必须有审计缓存，先核对再清理；
    ② 删除前**现场重算** (路径,SHA1) 并再次确认 旧⊆新，不一致就跳过；
    ③ 走 delete_item → 115 回收站，可恢复；④ 留清单，绝不碰「旧库独有实质文件」的条目；
    ⑤ `execute=False`（默认）只预览，不动任何数据。异步/可续跑：每条结论即时落盘。
    """
    from .egs_core import open_egs_db

    started = datetime.datetime.now()
    state_path = Path(state_path or (REPO_ROOT / 'status' / f'gal_old_audit_{year}.json'))
    result = {
        'action': 'old_clean', 'year': int(year), 'execute': bool(execute),
        'ok': True, 'status': 'complete', 'dry_run': not execute,
        'counts': {'candidates': 0, 'verified': 0, 'deleted': 0,
                   'verify_failed': 0, 'delete_failed': 0, 'skipped': 0},
        'items': [], 'artifacts': [], 'highlights': [], 'error': None,
    }
    if not state_path.exists():
        result.update(ok=False, status='error',
                      error=f'还没有 {year} 年的审计结论（{state_path.name}），请先跑「核对旧库」')
        return result
    try:
        cached = (json.loads(state_path.read_text(encoding='utf-8')).get('items') or {})
    except (OSError, ValueError) as exc:
        result.update(ok=False, status='error', error=f'审计结论无法读取：{exc}')
        return result

    candidates = {name: item for name, item in cached.items()
                  if item.get('verdict') == 'old_subset_new' and (item.get('counterpart') or {}).get('cid')}
    result['counts']['candidates'] = len(candidates)
    if not candidates:
        result['highlights'].append('没有可清理的冗余候选（需先有旧⊆新 的审计结论）')
        return result

    from .p115_client import delete_item

    conn = open_egs_db()
    done = 0
    try:
        for name, item in candidates.items():
            if deadline and time.time() > deadline:
                result.update(status='partial', partial=len(candidates) - done)
                result['highlights'].append(f'到时间上限，剩 {len(candidates) - done} 条未处理（可再点一次续跑）')
                break
            entry = _find_entry(conn, year, name)
            record = {'name': name, 'counterpart': item['counterpart'].get('folder_name'),
                      'verified': False, 'deleted': False, 'message': ''}
            if not entry:
                record['message'] = '115 里已找不到该旧库条目（可能已删）'
                result['counts']['skipped'] += 1
            else:
                probe = _analyze_entry(conn, year, entry, 90, per_item_timeout, deadline)
                old_set_ok = probe.get('verdict') == 'old_subset_new'
                record['verified'] = old_set_ok
                record['old_only_count'] = probe.get('old_only_count', 0)
                if not old_set_ok:
                    record['message'] = f'现场复验不通过（{probe.get("verdict")}），跳过'
                    result['counts']['verify_failed'] += 1
                elif not execute:
                    record['message'] = '预览：确认可删（未执行）'
                    result['counts']['verified'] += 1
                else:
                    result['counts']['verified'] += 1
                    res = delete_item(entry['cid'])
                    record['deleted'] = bool(res.get('success'))
                    record['message'] = '已删除（进回收站）' if record['deleted'] else str(res.get('message'))[:120]
                    result['counts']['deleted' if record['deleted'] else 'delete_failed'] += 1
            result['items'].append(record)
            done += 1
            if progress:
                try:
                    progress(done, len(candidates), name)
                except Exception:
                    pass
    finally:
        conn.close()

    c = result['counts']
    result['highlights'].append(
        f"{'预览' if not execute else '执行'}：候选 {c['candidates']} 条 → 现场复验通过 {c['verified']}、"
        f"删除 {c['deleted']}、复验不过 {c['verify_failed']}、删除失败 {c['delete_failed']}、跳过 {c['skipped']}")
    if not execute:
        result['highlights'].append('这是预览（execute=false）。确认无误后用 execute=true 再跑才会真删，删除进回收站可恢复。')
    result['finished_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    result['duration_s'] = int((datetime.datetime.now() - started).total_seconds())
    if write_manifest:
        out_dir = REPO_ROOT / 'db_backups'
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"gal_old_{year}_clean_{datetime.datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        result['artifacts'].append(str(path.relative_to(REPO_ROOT)))
    return result


def _find_entry(conn, year: int, name: str):
    """按名字在当前旧库目录里重新定位条目（清理前必须现场取，不信缓存里的 cid）。"""
    cid = resolve(f'{GAL_OLD_ROOT}/GAL-{year}')
    if not cid:
        return None
    for entry in _children(cid):
        if entry['n'] == name:
            return entry
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description='审计 / 清理 /GAL.old/GAL-YYYY')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--no-manifest', action='store_true', help='不写清单文件')
    parser.add_argument('--clean', action='store_true', help='清理冗余候选（默认只预览）')
    parser.add_argument('--execute', action='store_true', help='配合 --clean 才真正删除（进回收站）')
    args = parser.parse_args(argv)
    if args.clean:
        result = clean_old_year(args.year, execute=args.execute,
                                write_manifest=not args.no_manifest)
    else:
        result = audit_old_year(args.year, write_manifest=not args.no_manifest)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
