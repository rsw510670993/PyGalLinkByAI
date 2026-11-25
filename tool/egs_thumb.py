"""Getchu 封面下载：把封面包图存到 thumbnails/{getchu_id}.jpg。

只处理已经有 getchu_id 的记录（EGS 抓取时带回来的 id），按 id 直连带图，
零搜索、零误配；没有 getchu_id 的记录留给后续「按名搜索」阶段。
下载是幂等的：目标文件已存在就跳过，可随时中断续跑。
"""
from __future__ import annotations

import difflib
import html as html_lib
import os
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

import requests

from . import egs_extids
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
    """原图路径（下载中转用；压缩后会被删除，**不能**用来判断覆盖）。"""
    return thumb_dir() / f'{gid}.jpg'


def web_thumb_path(gid) -> Path:
    """Getchu 源最终产物：thumbnails/web/{gid}.webp。"""
    return thumb_dir() / 'web' / f'{gid}.webp'


def web_dlsite_thumb_path(pid) -> Path:
    """DLsite 源最终产物：thumbnails/web/dlsite/{pid}.webp。"""
    return thumb_dir() / 'web' / 'dlsite' / f'{pid}.webp'


def official_dir() -> Path:
    return thumb_dir() / 'official'


def official_thumb_path(egs_id) -> Path:
    return official_dir() / f'{egs_id}.jpg'


def web_official_thumb_path(egs_id) -> Path:
    return thumb_dir() / 'web' / 'official' / f'{egs_id}.webp'


def has_cover(row) -> bool:
    """该记录是否已有可用封面。

    判断基准是压缩后的 webp（原图 .jpg 在 convert 后即被删除，
    拿原图判断会把已覆盖的整库都当缺失）。
    """
    gid = str(row['getchu_id'] or '').strip()
    if gid and web_thumb_path(gid).is_file():
        return True
    pid = str(row['dlsite_id'] or '').strip()
    if pid and web_dlsite_thumb_path(pid).is_file():
        return True
    dmm = str(_row_get(row, 'dmm') or '').strip()
    if dmm and web_dmm_thumb_path(dmm).is_file():
        return True
    egs_id = _row_get(row, 'egs_id')
    return egs_id is not None and web_official_thumb_path(egs_id).is_file()


def _row_get(row, key):
    """安全取列：不同查询的 SELECT 列不完全一致。"""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def missing_kind(row) -> str:
    """缺口分类：getchu / dlsite / dmm / no_id（连 dmm 编号都没有）。"""
    gid = str(row['getchu_id'] or '').strip()
    pid = str(row['dlsite_id'] or '').strip()
    if gid:
        return 'getchu'
    if pid:
        return 'dlsite'
    if str(_row_get(row, 'dmm') or '').strip():
        return 'dmm'
    return 'no_id'


def pending_rows(conn, year: int | None = None, month: int | None = None,
                 limit: int = 0) -> list:
    """有 getchu_id 且**尚无任何封面**的记录。"""
    sql = ("SELECT egs_id, " + egs_extids.external_id_columns() + ", date, name "
           "FROM egs_games " "WHERE getchu_id IS NOT NULL AND getchu_id != ''")
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
    missing = [r for r in rows if not has_cover(r)]
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
            "SELECT " + egs_extids.external_id_columns() + " FROM egs_games"
        ).fetchall()
    finally:
        conn.close()
    total = len(rows)
    missing = [r for r in rows if not has_cover(r)]
    kinds = {'getchu': 0, 'dlsite': 0, 'dmm': 0, 'no_id': 0}
    for r in missing:
        kinds[missing_kind(r)] += 1
    have = total - len(missing)
    with_getchu = sum(1 for r in rows if str(r['getchu_id'] or '').strip())
    return {
        'total': total,
        'with_id': with_getchu,
        'with_cover': have,
        'missing': len(missing),
        'rate': round(have / total * 100, 2) if total else 0.0,
        'missing_getchu': kinds['getchu'],
        'missing_dlsite': kinds['dlsite'],
        'missing_dmm': kinds['dmm'],
        'missing_no_id': kinds['no_id'],
    }

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
    """Getchu 源封面是否已有成品（以 webp 为准）。"""
    gid = str(row['getchu_id'] or '').strip()
    return bool(gid) and web_thumb_path(gid).is_file()


def has_dlsite_cover(row) -> bool:
    """DLsite 源封面是否已有成品（以 webp 为准）。"""
    pid = str(row['dlsite_id'] or '').strip()
    return bool(pid) and web_dlsite_thumb_path(pid).is_file()


def missing_downloaded_count() -> int:
    """已下载记录里仍缺封面的条数（总控待办卡用）。"""
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = conn.execute(
            "SELECT egs_id, " + egs_extids.external_id_columns() + " FROM egs_games "
            "WHERE downloaded = 1"
        ).fetchall()
        return sum(1 for row in rows if not has_cover(row))
    except Exception:  # noqa: BLE001 - 面板统计失败不应拖垮 overview
        return -1
    finally:
        conn.close()


def pending_dlsite(conn, year: int | None = None, month: int | None = None,
                   limit: int = 0) -> list:
    """有 dlsite_id、且**尚无任何封面**的记录。"""
    sql = ("SELECT egs_id, " + egs_extids.external_id_columns() + ", date, name "
           "FROM egs_games " "WHERE dlsite_id IS NOT NULL AND dlsite_id != ''")
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
        if dlsite_site(r['dlsite_id']) and not has_cover(r)
    ]
    return missing[:limit] if limit else missing


DLSITE_CDN = 'https://img.dlsite.jp/modpub/images2/work/{sub}/{folder}/{pid}_img_{kind}.jpg'
# 前缀 → CDN 子目录：同人作品在 doujin，商业作品在 professional
DLSITE_CDN_SUB = {'RJ': 'doujin', 'VJ': 'professional', 'RE': 'doujin'}


def dlsite_cdn_urls(pid) -> list[str]:
    """按编号直接拼 CDN 封面地址（main 优先，sam 兜底）。

    2026-11-26 实测：已下架作品商品页返回 404，但图片仍在 CDN 上。
    目录规则＝前缀 + 编号向上取整到千位（RJ088973 → RJ089000）。
    """
    match = re.match(r'([A-Za-z]+)(\d+)', str(pid or '').strip())
    if not match:
        return []
    prefix, digits = match.group(1).upper(), match.group(2)
    sub = DLSITE_CDN_SUB.get(prefix)
    if not sub:
        return []
    upper = -(-int(digits) // 1000) * 1000
    folder = f'{prefix}{upper:0{len(digits)}d}'
    name = f'{prefix}{digits}'
    return [DLSITE_CDN.format(sub=sub, folder=folder, pid=name, kind=kind)
            for kind in ('main', 'sam')]


def _save_dlsite_image(pid, data: bytes) -> None:
    target = dlsite_thumb_path(pid)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    tmp.replace(target)


def download_dlsite(pid, session: requests.Session, timeout: int = 30):
    """取 DLsite 封面：① CDN 直连（下架作品也能取）② 商品页 og:image 兜底。"""
    site = dlsite_site(pid)
    if not site:
        return False, '未知 dlsite 前缀', 0
    last_error = '无可用地址'
    for url in dlsite_cdn_urls(pid):
        try:
            img = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f'CDN 网络错误: {exc}'
            continue
        if img.status_code != 200 or 'image' not in img.headers.get('Content-Type', ''):
            last_error = f'CDN HTTP {img.status_code}'
            continue
        if len(img.content) < MIN_BYTES:
            last_error = 'CDN 文件过小'
            continue
        _save_dlsite_image(pid, img.content)
        return True, f'cdn {len(img.content)} bytes', len(img.content)
    try:
        page = session.get(DLSITE_URL.format(site=site, pid=pid), timeout=timeout)
    except requests.RequestException as exc:
        return False, f'网络错误: {exc}', 0
    if page.status_code != 200:
        return False, f'HTTP {page.status_code}（CDN: {last_error}）', 0
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
    _save_dlsite_image(pid, img.content)
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
    """DLsite 源覆盖率：有 dlsite_id 的记录里，封面（任一源）是否已就绪。"""
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = conn.execute(
            "SELECT " + egs_extids.external_id_columns() + " FROM egs_games "
            "WHERE dlsite_id IS NOT NULL AND dlsite_id !=''"
        ).fetchall()
    finally:
        conn.close()
    have_dlsite = sum(1 for r in rows if has_dlsite_cover(r))
    have = sum(1 for r in rows if has_cover(r))
    return {'with_dlsite_id': len(rows), 'with_dlsite_cover': have_dlsite,
            'with_cover': have, 'missing': len(rows) - have}

# ---------------- 按名搜索（两个 id 都没有的记录） ----------------
# Getchu 是老 EUC-JP 站：搜索关键词必须用 euc-jp 编码传参，
# 传 UTF-8 会被服务端当成 EUC-JP 解出乱码，返回 0 结果。
GETCHU_SEARCH_URL = 'https://www.getchu.com/php/nsearch.phtml?search_keyword={kw}'
GETCHU_SEARCH_PAGE = 'https://www.getchu.com/php/nsearch.phtml?search_keyword={kw}&search_page={page}'
# 非游戏介质（音乐/DVD/周边/书籍）即使标题撞上也不要，避免配到原声集、画集。
NON_GAME_MEDIA = {'CD', 'DVD', 'グッズ', '書籍', 'アニメ', '音楽', 'BOOK'}
ITEM_ANCHOR_RE = re.compile(
    r'soft\.phtml\?id=(\d+)"\s*class="blueb"\s*>(.*?)</A>', re.S | re.I)
BRAND_RE = re.compile(r'ブランド名：\s*<A[^>]*>(.*?)</A>', re.S)
DATE_RE = re.compile(r'発売日：\s*(\d{4}/\d{2}/\d{2})')
MEDIA_RE = re.compile(r'<span class="orangeb">\[(.*?)\]</span>', re.S)
_TITLE_STRIP = '~〜～-—–―_!！?？.,、。:：;；\'"“”‘’()（）[]【】「」『』/／\\|・･★☆♪♡… 　'


def norm_text(value) -> str:
    """归一化：NFKC、小写、去空白与装饰标点，保留假名/汉字/字母数字。"""
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.lower()
    return ''.join(ch for ch in text if ch not in _TITLE_STRIP)


def norm_brand(value) -> str:
    """公司/品牌归一化：NFKC、小写、去空格与括号内注释。"""
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = re.sub(r'[（(\[【].*?[)）\]】]', '', text)
    text = text.lower().replace(' ', '').replace('　', '')
    return ''.join(ch for ch in text if ch not in '・･.,、。')


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


TITLE_SPAN_MIN = 0.60      # 包含判定的长度比下限（原 0.70）
TITLE_RATIO_MIN = 0.82     # 相似度下限（原 0.86）
CONTAIN_SCORE = 0.92       # 包含命中给的分数
EMPTY_BRAND_TITLE_MIN = 0.90  # 候选品牌为空时，标题需达到的分数


def title_match(target: str, candidate: str) -> tuple[bool, float]:
    """标题核心匹配：完全一致、包含（长度比≥0.60）、或相似度≥0.82。

    2026-11-26 放宽：原 0.70/0.86 会把「作品名 + 特典后缀」这类正确候选否掉
    （40193 紳士ドライバー 长度比 0.68、相似度 0.80，唯一候选被丢）。
    """
    a, b = norm_text(target), norm_text(candidate)
    if not a or not b:
        return False, 0.0
    if a == b:
        return True, 1.0
    ratio = _similar(a, b)
    if a in b or b in a:
        span = min(len(a), len(b)) / max(len(a), len(b))
        if span >= TITLE_SPAN_MIN:
            return True, max(ratio, CONTAIN_SCORE)
    return (ratio >= TITLE_RATIO_MIN), ratio


def brand_match(target: str, candidate: str) -> tuple[bool, float]:
    """公司/品牌匹配：包含或相似度≥0.8（品牌常有 株式会社/． 等噪声）。"""
    a, b = norm_brand(target), norm_brand(candidate)
    if not a or not b:
        return False, 0.0
    if a == b:
        return True, 1.0
    if a in b or b in a:
        return True, 0.95
    ratio = _similar(a, b)
    return (ratio >= 0.8), ratio


def brand_state(target: str, candidate: str) -> tuple[str, float]:
    """品牌三态：match / empty（候选页面没写品牌）/ miss。

    Getchu 的同人作品页经常没有「ブランド名」链接，空品牌不能当成不匹配。
    """
    a, b = norm_brand(target), norm_brand(candidate)
    if not a or not b:
        return 'empty', 0.0
    ok, score = brand_match(target, candidate)
    return ('match', score) if ok else ('miss', score)


def parse_getchu_search(html: str) -> list[dict]:
    """解析 Getchu 搜索结果页为条目列表。"""
    items = []
    for match in ITEM_ANCHOR_RE.finditer(html):
        gid = match.group(1)
        title = re.sub(r'<[^>]+>', '', match.group(2))
        title = html_lib.unescape(title).strip()
        window = html[match.end():match.end() + 2600]
        brand_m = BRAND_RE.search(window)
        date_m = DATE_RE.search(window)
        media_m = MEDIA_RE.search(window)
        items.append({
            'getchu_id': gid,
            'title': title,
            'brand': html_lib.unescape(brand_m.group(1)).strip() if brand_m else '',
            'release': date_m.group(1) if date_m else '',
            'media': media_m.group(1).strip() if media_m else '',
        })
    return items


def search_getchu(name: str, session: requests.Session, timeout: int = 30,
                  pages: int = 1):
    """按作品名搜索 Getchu，返回 (条目列表, 错误信息)。"""
    kw = urllib.parse.quote(str(name).encode('euc-jp', 'ignore'))
    results: list[dict] = []
    for page in range(1, pages + 1):
        url = (GETCHU_SEARCH_URL if page == 1
               else GETCHU_SEARCH_PAGE.format(kw=kw, page=page)).format(kw=kw)
        try:
            resp = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            return results, f'网络错误: {exc}'
        if resp.status_code != 200:
            return results, f'HTTP {resp.status_code}'
        html = resp.content.decode('euc-jp', errors='replace')
        results.extend(parse_getchu_search(html))
        if page > 1 and not parse_getchu_search(html):
            break
    return results, ''


def pick_getchu_match(row, results: list[dict], min_score: float = EMPTY_BRAND_TITLE_MIN):
    """从搜索结果里挑唯一可信的匹配：标题为主，品牌缺失不否决。

    品牌为空（Getchu 同人页常见）时改用标题分数下限兜底；
    并列最高分时优先品牌命中的候选，仍并列则放弃（避免歧义配错）。
    返回 (match, evidence)；没有可靠匹配时 match 为 None。
    """
    target_name = _row_get(row, 'name') or _row_get(row, 'egs_name') or ''
    target_brand = _row_get(row, 'company') or _row_get(row, 'egs_company') or ''
    scored = []
    for item in results:
        if (item.get('media') or '').upper() in NON_GAME_MEDIA:
            continue
        t_ok, t_score = title_match(target_name, item['title'])
        if not t_ok:
            continue
        state, b_score = brand_state(target_brand, item['brand'])
        if state == 'miss':
            continue
        if state == 'empty' and t_score < min_score:
            continue
        item['title_score'] = round(t_score, 3)
        item['brand_state'] = state
        item['brand_score'] = round(b_score, 3)
        scored.append(item)
    if not scored:
        return None, {'reason': '无标题（+品牌）匹配候选', 'candidates': len(results)}
    scored.sort(key=lambda it: (-it['title_score'], -it['brand_score']))
    best = scored[0]
    # 同分且品牌状态相同才视为歧义；一条品牌命中、一条品牌缺失时取命中的
    tied = [it for it in scored[1:]
            if it['title_score'] == best['title_score']
            and it['brand_state'] == best['brand_state']]
    if tied:
        return None, {'reason': '多个候选并列，放弃', 'candidates': len(results)}
    reason = ('标题+品牌匹配' if best['brand_state'] == 'match'
              else '标题匹配（候选无品牌）')
    return best, {'reason': reason, 'candidates': len(results)}



# ---------------- 作品官网 og:image（第三来源，兜底） ----------------
OG_IMAGE_PATTERNS = (
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)', re.I),
)
PAGE_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.S | re.I)
OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', re.I)


def parse_og_image(html: str) -> str:
    """从 HTML 里取 og:image / twitter:image，取不到返回空串。"""
    for pattern in OG_IMAGE_PATTERNS:
        match = pattern.search(html)
        if match:
            return html_lib.unescape(match.group(1)).strip()
    return ''


def page_is_about(html: str, name: str, company: str) -> bool:
    """页面自述（title / og:title）是否与本作品或品牌相关。

    官网改版、域名停放、作品页 404 都会导致标题不符，此时宁可不采用。
    """
    titles = []
    og = OG_TITLE_RE.search(html)
    if og:
        titles.append(html_lib.unescape(og.group(1)))
    tm = PAGE_TITLE_RE.search(html)
    if tm:
        titles.append(html_lib.unescape(re.sub(r'<[^>]+>', '', tm.group(1))))
    for title in titles:
        t_ok, t_score = title_match(name, title)
        if t_ok:
            return True
        nt = norm_text(title)
        nb = norm_brand(company)
        if nb and len(nb) >= 3 and nb in nt:
            return True
        if norm_text(name) and norm_text(name) in nt:
            return True
    return False


def image_looks_like_package(data: bytes, min_width: int = 200,
                             min_height: int = 200) -> tuple[bool, str]:
    """包图基本是竖版；横版大图多半是横幅/logo，判为不合格。"""
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
    except Exception as exc:  # noqa: BLE001 - 图片可能是任意损坏格式
        return False, f'无法解析图片: {exc}'
    if width < min_width or height < min_height:
        return False, f'尺寸过小 {width}x{height}'
    if height < width * 0.95:
        return False, f'非竖版 {width}x{height}'
    return True, f'{width}x{height}'

# ---------------- DMM 编号直连（无 getchu/dlsite 但 EGS 带 dmm 编号） ----------------
# 编号来自 EGS 关联，不做任何按名匹配，零配错；只需排除 DMM 的占位小图。
# ps = 竖版包图（143x200 级），pl = 800x600 横向商品宣传图（多格插图，仅兜底）。
DMM_PATTERNS_PACKAGE = (
    'https://pics.dmm.co.jp/mono/game/{d}/{d}ps.jpg',
    'https://pics.dmm.co.jp/digital/pcgame/{d}/{d}ps.jpg',
)
DMM_PATTERNS_FALLBACK = (
    'https://pics.dmm.co.jp/mono/game/{d}/{d}pl.jpg',
    'https://pics.dmm.co.jp/digital/pcgame/{d}/{d}pl.jpg',
)
DMM_PLACEHOLDER_BYTES = 6000   # 2732/2517 是「无图」占位图；真封面远大于此


def dmm_dir() -> Path:
    return thumb_dir() / 'dmm'


def dmm_thumb_path(dmm) -> Path:
    return dmm_dir() / f'{dmm}.jpg'


def web_dmm_thumb_path(dmm) -> Path:
    return thumb_dir() / 'web' / 'dmm' / f'{dmm}.webp'


def has_dmm_cover(row) -> bool:
    dmm = str(row['dmm'] or '').strip()
    return bool(dmm) and web_dmm_thumb_path(dmm).is_file()


def pending_dmm(conn, limit: int = 0) -> list:
    """没有 getchu/dlsite id、但有 dmm 编号且尚无封面的记录。"""
    rows = conn.execute(
        "SELECT egs_id, " + egs_extids.external_id_columns() + ", date, name "
        "FROM egs_games WHERE dmm IS NOT NULL AND dmm != '' ORDER BY date, egs_id"
    ).fetchall()
    missing = [r for r in rows if missing_kind(r) == 'dmm' and not has_cover(r)]
    return missing[:limit] if limit else missing


def download_dmm(dmm, session: requests.Session, timeout: int = 30,
                 allow_landscape: bool = True):
    """按 DMM 编号取封面：优先竖版包图（ps），退回 800x600 商品图（pl）。

    返回 (ok, message, bytes, url)。
    """
    code = str(dmm or '').strip()
    if not code:
        return False, '空 dmm 编号', 0, ''
    best = None
    rounds = [(DMM_PATTERNS_PACKAGE, True)]
    if allow_landscape:
        rounds.append((DMM_PATTERNS_FALLBACK, False))
    for patterns, need_portrait in rounds:
        for pattern in patterns:
            url = pattern.format(d=code)
            try:
                resp = session.get(url, timeout=timeout)
            except requests.RequestException:
                continue
            if resp.status_code != 200 or 'image' not in resp.headers.get('Content-Type', ''):
                continue
            data = resp.content
            if len(data) < DMM_PLACEHOLDER_BYTES:
                continue
            if need_portrait:
                ok, _info = image_looks_like_package(data, 120, 160)
                if not ok:
                    continue
            if best is None or len(data) > len(best[1]):
                best = (url, data)
        if best is not None:
            break
    if best is None:
        return False, '所有 DMM 路径均无有效封面', 0, ''
    url, data = best
    target = dmm_thumb_path(code)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    tmp.replace(target)
    return True, f'{len(data)} bytes', len(data), url


def run_dmm(limit: int = 0, interval: float = 1.0, should_stop=None,
            logger=None) -> dict:
    """按 DMM 编号批量补封面（竖版包图优先，横向商品图兜底）。"""
    log = logger or (lambda msg: None)
    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        rows = pending_dmm(conn, limit)
    finally:
        conn.close()

    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Referer': 'https://www.dmm.co.jp/'})
    stats = {'status': 'success', 'mode': 'dmm', 'total': len(rows), 'ok': 0,
             'failed': 0, 'failed_ids': [], 'last_error': '',
             'started_at': now_ts(), 'finished_at': None}
    log(f'待下载 {len(rows)} 张 DMM 封面')
    for i, row in enumerate(rows):
        if should_stop and should_stop():
            stats['status'] = 'stopped'
            break
        code = str(row['dmm']).strip()
        ok, message, _size, _url = download_dmm(code, session)
        if ok:
            stats['ok'] += 1
            log(f'[{i + 1}/{len(rows)}] {code} ✓ {message} {row["name"]}')
        else:
            stats['failed'] += 1
            stats['failed_ids'].append(code)
            stats['last_error'] = message
            log(f'[{i + 1}/{len(rows)}] {code} ✗ {message} {row["name"]}')
        if interval and i + 1 < len(rows):
            time.sleep(interval)
    stats['finished_at'] = now_ts()
    _save_state(stats)
    return stats


def download_official(egs_id, url, session: requests.Session,
                      timeout: int = 15) -> tuple[bool, str, int]:
    """从作品官网抓 og:image 包图，存到 thumbnails/official/{egs_id}.jpg。

    返回 (ok, message, bytes)。
    """
    return download_official_with_data(egs_id, url, session, timeout)


def download_official_with_data(egs_id, url, session, timeout=15):
    code = str(egs_id)
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return False, f'官网网络错误: {exc}', 0
    if resp.status_code != 200:
        return False, f'官网 HTTP {resp.status_code}', 0
    resp.encoding = resp.apparent_encoding or 'utf-8'
    image_url = parse_og_image(resp.text)
    if not image_url:
        return False, '无 og:image', 0
    if image_url.startswith('//'):
        image_url = 'https:' + image_url
    elif image_url.startswith('/'):
        base = urllib.parse.urlsplit(resp.url)
        image_url = f'{base.scheme}://{base.netloc}{image_url}'
    try:
        img = session.get(image_url, timeout=timeout)
    except requests.RequestException as exc:
        return False, f'图片网络错误: {exc}', 0
    if img.status_code != 200 or 'image' not in img.headers.get('Content-Type', ''):
        return False, f'图片 HTTP {img.status_code}', 0
    ok, info = image_looks_like_package(img.content)
    if not ok:
        return False, f'非包图: {info}', len(img.content)
    target = official_thumb_path(code)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    tmp.write_bytes(img.content)
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    tmp.replace(target)
    return True, f'{info} {len(img.content)} bytes', len(img.content)


# ---------------- 补 ID：从种子名 / 115 目录名提取 DLsite 编号 ----------------
# 编号来自我们自己下载的目录名或种子名，不是按名猜，零误配风险。
RJ_ID_RE = re.compile(r'((?:RJ|VJ|RE)\d{6,10})', re.I)


def extract_dlsite_id(text) -> str:
    match = RJ_ID_RE.search(str(text or ''))
    return match.group(1).upper() if match else ''


def scan_dlsite_ids_from_dirs(conn, dry_run: bool = True, limit: int = 0) -> dict:
    """已下载但无任何外部 ID 的记录：从种子名 / 115 目录名提取 DLsite 编号。"""
    sql = """
        SELECT g.egs_id, g.name, g.torrent_name, g.nyaa_name,
               COALESCE(f.folder_name,'') AS folder_name,
               COALESCE(f.folder_path,'') AS folder_path
          FROM egs_games g
          LEFT JOIN egs_115_folders f ON f.date=g.date AND f.name=g.name
         WHERE g.downloaded=1
           AND COALESCE(g.getchu_id,'')='' AND COALESCE(g.dlsite_id,'')=''
           AND COALESCE(g.dmm,'')=''
         ORDER BY g.egs_id
    """
    rows = conn.execute(sql).fetchall()
    items = []
    for row in rows:
        blob = ' '.join(str(_row_get(row, key) or '') for key in
                        ('torrent_name', 'nyaa_name', 'folder_name', 'folder_path'))
        pid = extract_dlsite_id(blob)
        if not pid:
            continue
        items.append({'egs_id': row['egs_id'], 'name': row['name'], 'dlsite_id': pid})
        if limit and len(items) >= limit:
            break
    written = 0
    if not dry_run:
        for item in items:
            res = egs_extids.attach_external_id(
                conn, item['egs_id'], 'dlsite', item['dlsite_id'], origin='dir_scan')
            if res.get('changed') or res.get('legacy_written'):
                written += 1
                item['written'] = True
        conn.commit()
    return {'status': 'success', 'mode': 'dir_scan', 'dry_run': dry_run,
            'scanned': len(rows), 'extracted': len(items), 'written': written,
            'items': items}


def run_namesearch_getchu(conn, year: int | None = None, month: int | None = None,
                          limit: int = 0, dry_run: bool = True,
                          interval: float = 1.5, should_stop=None,
                          logger=None) -> dict:
    """对没有任何外部 ID 的记录按名搜索 Getchu，补 getchu_id。

    只处理 getchu/dlsite/dmm 全空的记录：已经有 ID 的不该再按名猜。
    """
    log = logger or (lambda msg: None)
    sql = ("SELECT egs_id, name, company, date FROM egs_games "
           "WHERE COALESCE(getchu_id,'')='' AND COALESCE(dlsite_id,'')='' "
           "AND COALESCE(dmm,'')=''")
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
    if limit:
        rows = rows[:limit]

    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Referer': 'https://www.getchu.com/'})
    stats = {'status': 'success', 'mode': 'getchu_search', 'dry_run': dry_run,
             'year': year, 'month': month, 'total': len(rows),
             'matched': 0, 'written': 0, 'items': []}
    for i, row in enumerate(rows):
        if should_stop and should_stop():
            stats['status'] = 'stopped'
            break
        hits, err = search_getchu(row['name'], session)
        match, evidence = pick_getchu_match(row, hits)
        item = {'egs_id': row['egs_id'], 'name': row['name'], 'hits': len(hits),
                'reason': evidence.get('reason'), 'error': err, 'match': None}
        if match is not None:
            stats['matched'] += 1
            item['match'] = {'getchu_id': match['getchu_id'], 'title': match['title'],
                             'score': match.get('title_score'),
                             'brand_state': match.get('brand_state')}
            if not dry_run:
                res = egs_extids.attach_external_id(
                    conn, row['egs_id'], 'getchu', match['getchu_id'],
                    origin='getchu_search')
                item['written'] = bool(res.get('changed') or res.get('legacy_written'))
                if item['written']:
                    stats['written'] += 1
        stats['items'].append(item)
        log(f'[{i + 1}/{len(rows)}] {row["egs_id"]} hits={len(hits)} '
            f'{"✓" if match else "✗"} {row["name"]}')
        if i + 1 < len(rows):
            time.sleep(interval)
    if not dry_run:
        conn.commit()
    return stats
