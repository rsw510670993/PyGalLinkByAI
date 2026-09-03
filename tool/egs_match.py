"""sukebei.nyaa 候选评分与择优策略（Phase 2）。

输入：EGS_games 一行（已按 Phase 1 去重，一行一作品）+ nyaa 候选列表
输出：每个候选的得分与维度明细；择优回填阈值 THRESHOLD

评分维度（满分约 75）：
- 游戏名匹配: 完整包含 +30 / 最长公共块≥85% +25 / ≥60% +12
- 公司名包含: +10
- 时间接近:   nyaa发布日距EGS发售日 ≤7天+15 / ≤30天+10 / ≤60天+5；
              无发布日时退化为标题[YYMMDD]月份匹配 +10
- 发布者:     girlcelly +10 / 2D.G.F. +8
"""
import re
from datetime import datetime
from difflib import SequenceMatcher

THRESHOLD = 40.0


def _norm(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\s+", "", s)
    for a, b in (("〜", "~"), ("～", "~"), ("〰", "~"), ("＋", "+"), ("＆", "&"),
                 ("：", ":"), ("；", ";"), ("！", "!"), ("？", "?"),
                 ("＊", "*"), ("／", "/"), ("・", ""), ("　", ""),
                 ("＝", "="), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("（", ""), ("）", ""), ("、", ""), ("《", ""), ("》", "")):
        s = s.replace(a, b)
    # 连字符/破折号与规避审查的填充符（getchu用○、nyaa用●等）两侧统一去除
    s = re.sub(r"[-－−―‐﹣]", "", s)
    s = re.sub(r"[○●◎◯⭘]", "", s)
    # 波浪线（名字两侧装饰）与全半角数字/字母统一
    s = re.sub(r"[~〜～〰]", "", s)
    for a, b in (("０","0"),("１","1"),("２","2"),("３","3"),("４","4"),
                 ("５","5"),("６","6"),("７","7"),("８","8"),("９","9"),
                 ("Ａ","a"),("Ｂ","b"),("Ｃ","c"),("Ｄ","d"),("Ｅ","e"),
                 ("Ｆ","f"),("Ｇ","g"),("Ｈ","h"),("Ｉ","i"),("Ｊ","j"),
                 ("Ｋ","k"),("Ｌ","l"),("Ｍ","m"),("Ｎ","n"),("Ｏ","o"),
                 ("Ｐ","p"),("Ｑ","q"),("Ｒ","r"),("Ｓ","s"),("Ｔ","t"),
                 ("Ｕ","u"),("Ｖ","v"),("Ｗ","w"),("Ｘ","x"),("Ｙ","y"),("Ｚ","z"),
                 ("ａ","a"),("ｂ","b"),("ｃ","c"),("ｄ","d"),("ｅ","e"),
                 ("ｆ","f"),("ｇ","g"),("ｈ","h"),("ｉ","i"),("ｊ","j"),
                 ("ｋ","k"),("ｌ","l"),("ｍ","m"),("ｎ","n"),("ｏ","o"),
                 ("ｐ","p"),("ｑ","q"),("ｒ","r"),("ｓ","s"),("ｔ","t"),
                 ("ｕ","u"),("ｖ","v"),("ｗ","w"),("ｘ","x"),("ｙ","y"),("ｚ","z")):
        s = s.replace(a, b)
    return s


def extract_infohash(magnet):
    if not magnet:
        return None
    m = re.search(r"urn:btih:([A-Za-z0-9]+)", magnet)
    if not m:
        return None
    btih = m.group(1)
    if re.fullmatch(r"[A-Fa-f0-9]{40}", btih):
        return btih.lower()
    return btih  # base32 等形式，原样保留


def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def score_candidate(game, cand):
    """game: {name, company, date, release_date}; cand: {nyaa_title, nyaa_date, ...}

    返回 (total, detail_dict)。
    """
    title = cand.get("nyaa_title") or ""
    nt = _norm(title)
    detail = {}
    total = 0.0

    gnorm = _norm(game.get("name") or "")
    if len(gnorm) >= 4 and nt:
        if gnorm in nt:
            total += 30
            detail["name_exact"] = 30
        else:
            m = SequenceMatcher(None, gnorm, nt).find_longest_match(
                0, len(gnorm), 0, len(nt)
            )
            frac = m.size / len(gnorm)
            if frac >= 0.85:
                total += 25
                detail["name_partial"] = round(frac, 2)
            else:
                # 主标题拆分匹配：getchu名常为「主标题 -副标题-」，
                # nyaa标题可能只含主标题+版次名（副标题与版次名不同属正常）
                main = _norm(re.split(r"[〜～~\-－]", game.get("name") or "", 1)[0])
                if len(main) >= 10 and main in nt:
                    total += 25
                    detail["name_main"] = 25
                elif frac >= 0.6:
                    total += 12
                    detail["name_partial"] = round(frac, 2)

    comp = _norm(game.get("company") or "")
    if len(comp) >= 3 and comp in nt:
        total += 10
        detail["company"] = 10

    pub = _parse_dt(cand.get("nyaa_date"))
    rel = _parse_dt(game.get("release_date"))
    if not rel and game.get("date"):
        rel = _parse_dt(game["date"] + "-15")  # 无发售日时用当月中旬兜底
    if pub and rel:
        d = abs((pub.date() - rel.date()).days)
        if d <= 7:
            total += 15
            detail["date"] = f"±{d}d"
        elif d <= 30:
            total += 10
            detail["date"] = f"±{d}d"
        elif d <= 60:
            total += 5
            detail["date"] = f"±{d}d"
    else:
        ym = (game.get("date") or "").replace("-", "")
        for c in re.findall(r"\[(\d{6})\]", title):
            if ("20" + c[:2] + c[2:4]) == ym:
                total += 10
                detail["date_code"] = 10
                break

    tl = title.lower()
    if "girlcelly" in tl:
        total += 10
        detail["publisher"] = "girlcelly"
    elif "2d.g.f." in tl or "2dgf" in nt:
        total += 8
        detail["publisher"] = "2D.G.F."

    return round(total, 1), detail


def select_best(game, candidates, threshold=THRESHOLD):
    """返回 (best, best_score, best_detail) 或 (None, max_score, None)。"""
    best = None
    best_score = -1.0
    best_detail = None
    max_score = 0.0
    for c in candidates:
        s, d = score_candidate(game, c)
        c["score"] = s
        c["score_detail"] = d
        max_score = max(max_score, s)
        if s > best_score:
            best, best_score, best_detail = c, s, d
    if best is not None and best_score >= threshold:
        return best, best_score, best_detail
    return None, max_score, None
