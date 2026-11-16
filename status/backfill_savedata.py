#!/usr/bin/env python3
"""批量补全 CG 存档：seiya/sagaoz 索引 -> 下载 -> 直传 115 游戏目录 -> 落库。

用法（必须用装了 p115client 0.0.9 的解释器，项目 .venv 的 0.0.8 上传会 405）：
  status/venv115/bin/python status/backfill_savedata.py --limit 100          # dry-run
  status/venv115/bin/python status/backfill_savedata.py --limit 100 --execute

断点续跑：成功记录写入 egs_savedata 表，已 ok 的会跳过；本地下载包处理完即删。
"""
import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "egs.db"
CACHE = ROOT / "status" / "savedata_index.json"
LOG = ROOT / "status" / "savedata_backfill.log"
DL_DIR = Path("/tmp/savedata_dl")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm(s):
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.split(r"[（(]", s)[0]
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s\u3000・･!！?？,，.。、~～\-－―–—:：;；'\"“”‘’\[\]【】<>《》+＋&＆/／|｜_＿×]", "", s)
    return s.lower()


def curl(url, timeout=40, decode="utf-8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if decode == "cp932":
                return raw.decode("cp932", errors="ignore")
            return raw.decode("utf-8", errors="ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + i)
    raise last


def fetch_seiya():
    txt = curl("https://seiya-saiga.com/save.html", decode="cp932")
    out = []
    for name, content, url in re.findall(
        r'<TR>\s*<td align="left"[^>]*>(.*?)</td>\s*<TD align="center"[^>]*>(.*?)</TD>\s*<TD align="center"[^>]*>.*?href="(save/[^"]+\.zip)"',
        txt, re.S | re.I,
    ):
        nm = re.sub(r"<[^>]+>", "", name).strip()
        if not nm:
            continue
        out.append({
            "name": html.unescape(nm),
            "content": re.sub(r"<[^>]+>", "", content).strip(),
            "url": "https://seiya-saiga.com/" + url,
            "src": "seiya",
        })
    return out


def fetch_sagaoz():
    idx = curl("https://sagaoz.net/savedata/", decode="cp932")
    pages = sorted(set(re.findall(r"https://sagaoz\.net/savedata/[a-z0-9]+/[a-z0-9]+\.htm", idx)))
    out = []
    for u in pages:
        part = u.split("/savedata/")[1]
        d = part.split("/")[0]
        try:
            t = curl(u, decode="cp932")
        except Exception as e:  # noqa: BLE001
            log(f"sagaoz 页面失败 {u}: {e}")
            continue
        for tr in re.findall(r"<TR>(.*?)</TR>", t, re.S):
            m = re.search(r'<A href="([^"]+\.(?:zip|ZIP|lzh|LZH))">', tr)
            if not m:
                continue
            td = re.search(r'<TD align="left">(.*?)</TD>', tr, re.S)
            if not td:
                continue
            body = re.sub(r'^<FONT size="-2">.*?</FONT>\s*<BR>', "", td.group(1), flags=re.S)
            nm = re.split(r"<BR>", body)[0]
            nm = re.sub(r"<[^>]+>", "", nm).strip()
            if not nm:
                continue
            tds = re.findall(r"<TD align=[^>]*>(.*?)</TD>", tr, re.S)
            content = re.sub(r"<[^>]+>", "", tds[-1]).strip() if len(tds) >= 3 else ""
            out.append({
                "name": html.unescape(nm),
                "content": content[:40],
                "url": f"https://sagaoz.net/savedata/{d}/{m.group(1)}",
                "src": "sagaoz",
            })
    return out


def load_index(refresh=False):
    if not refresh and CACHE.exists() and time.time() - CACHE.stat().st_mtime < 86400:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    log("抓取索引 seiya + sagaoz ...")
    entries = fetch_seiya() + fetch_sagaoz()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    log(f"索引条目 {len(entries)}")
    return entries


def cookies_path():
    try:
        cfg = json.loads((ROOT / "tool" / "config.json").read_text(encoding="utf-8"))
        p = cfg.get("115_cookies_path")
        if p:
            return p if os.path.isabs(p) else str(ROOT / "tool" / p)
    except Exception:  # noqa: BLE001
        pass
    return str(ROOT / "tool" / "115-cookies.txt")


def make_client():
    from p115client import P115Client  # noqa: PLC0415
    text = Path(cookies_path()).read_text(encoding="utf-8").strip()
    try:
        return P115Client(cookies=text)
    except TypeError:
        return P115Client(Path(cookies_path()), check_for_relogin=True)


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS egs_savedata (
        egs_id INTEGER PRIMARY KEY,
        egs_name TEXT, source TEXT, url TEXT, sha1 TEXT, size INTEGER,
        content_tag TEXT, folder_cid TEXT, pickcode TEXT, filename TEXT,
        status TEXT DEFAULT 'ok', uploaded_at TEXT, note TEXT
    )""")
    conn.commit()
    return conn


def build_jobs(conn, limit, year=None):
    eg = {}
    q = "SELECT egs_id, egs_name, date, downloaded FROM egs_games WHERE downloaded=1"
    if year:
        q += f" AND date LIKE '{year}%'"
    for eid, name, date, dl in conn.execute(q):
        k = norm(name)
        if k and k not in eg:
            eg[k] = (eid, name, date)
    # status='skip' 表示源站失效/存档错配，已人工判定不再重试
    done = set(r[0] for r in conn.execute("SELECT egs_id FROM egs_savedata WHERE status IN ('ok','skip')"))
    best = {}
    for e in load_index():
        k = norm(e["name"])
        if k not in eg:
            continue
        eid = eg[k][0]
        if eid in done:
            continue
        cur = best.get(eid)
        if cur is None or (cur["src"] == "sagaoz" and e["src"] == "seiya"):
            best[eid] = {**e, "eid": eid, "egs_name": eg[k][1], "date": eg[k][2]}
    jobs = []
    for eid, j in best.items():
        r = conn.execute(
            "SELECT cid FROM egs_115_folders WHERE date=? AND name=?", (j["date"], j["egs_name"])
        ).fetchone()
        if r and r[0]:
            j["cid"] = str(r[0])
            jobs.append(j)
    jobs.sort(key=lambda j: j["date"] or "")
    return jobs[:limit]


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for i in range(2):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            if data:
                dest.write_bytes(data)
                return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def verify(path):
    if not zipfile.is_zipfile(path):
        return False, "not zip"
    try:
        z = zipfile.ZipFile(path)
        names = z.namelist()
        kinds = {os.path.splitext(n)[1].lower() for n in names}
        save_kinds = {".dat", ".ksd", ".sav", ".gdb", ".json", ".odn", ".reg", ".data", ".save", ".mss"}
        if names and (kinds & save_kinds or any("readme" in n.lower() or n.endswith(".txt") for n in names)):
            return True, f"{len(names)} files"
        return False, "no save files"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def sanitize(name, maxlen=90):
    s = re.sub(r'[\\/:*?"<>|]', "_", name)
    return s[:maxlen]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--year", default=None)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--delay", type=float, default=0.25, help="每个文件上传后的间隔秒数，限流时调大")
    args = ap.parse_args()

    conn = db()
    jobs = build_jobs(conn, args.limit, args.year)
    log(f"dry-run 候选 {len(jobs)} 个" if not args.execute else f"开始执行 {len(jobs)} 个")
    if not jobs:
        return
    if not args.execute:
        for j in jobs[:10]:
            log(f"  {j['eid']} {j['date']} {j['egs_name'][:40]} | {j['src']} {j['url'][:70]}")
        return

    client = make_client()
    DL_DIR.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    for i, j in enumerate(jobs, 1):
        eid = j["eid"]
        tmp = DL_DIR / f"{eid}.zip"
        tag = f"{j['date'][:7]} {j['egs_name'][:34]}"
        try:
            if not download(j["url"], tmp):
                fail += 1
                log(f"[{i}/{len(jobs)}] DL-FAIL {eid} {tag}")
                continue
            good, note = verify(tmp)
            size = tmp.stat().st_size
            if not good:
                fail += 1
                log(f"[{i}/{len(jobs)}] BAD-ZIP {eid} {note}")
                # 落一条 skip 记录：源站给的是 LZH/坏包，已判定不可用；
                # 否则 done 集合不含它，每批都会重新下载同一个坏源再失败。
                conn.execute(
                    "INSERT OR REPLACE INTO egs_savedata (egs_id,egs_name,source,url,size,"
                    "content_tag,folder_cid,status,uploaded_at,note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (eid, j["egs_name"], j["src"], j["url"], size, j.get("content", ""),
                     j["cid"], "skip", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     f"BAD-ZIP {note}"),
                )
                conn.commit()
                tmp.unlink(missing_ok=True)
                continue
            sha = hashlib.sha1(tmp.read_bytes()).hexdigest()
            fn = f"全CG存档_{sanitize(j['egs_name'])}.zip"
            resp = client.upload_file(str(tmp), pid=int(j["cid"]), filename=fn)
            if not (isinstance(resp, dict) and resp.get("state")):
                fail += 1
                log(f"[{i}/{len(jobs)}] UPLOAD-FAIL {eid} {str(resp)[:120]}")
                tmp.unlink(missing_ok=True)
                continue
            conn.execute(
                "INSERT OR REPLACE INTO egs_savedata (egs_id,egs_name,source,url,sha1,size,content_tag,"
                "folder_cid,pickcode,filename,status,uploaded_at,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, j["egs_name"], j["src"], j["url"], sha, size, j.get("content", ""),
                 j["cid"], str(resp.get("pickcode", "")), fn, "ok",
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note),
            )
            conn.commit()
            ok += 1
            tmp.unlink(missing_ok=True)
            if i % 10 == 0 or i == len(jobs):
                log(f"[{i}/{len(jobs)}] ok={ok} fail={fail}")
            time.sleep(max(0.05, args.delay))
        except Exception as e:  # noqa: BLE001
            fail += 1
            log(f"[{i}/{len(jobs)}] ERR {eid} {type(e).__name__}: {str(e)[:120]}")
            tmp.unlink(missing_ok=True)
    log(f"完成：成功 {ok} / 失败 {fail} / 跳过 {skip}（本批 {len(jobs)}）")
    print(f"RESULT ok={ok} fail={fail} batch={len(jobs)}")


if __name__ == "__main__":
    main()
