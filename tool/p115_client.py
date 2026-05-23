import base64
import json
import os
import re
import sys
from pathlib import Path

from .runtime import ensure_home_env, repo_root


def _tool_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _import_p115client():
    ensure_home_env(repo_root())
    from p115client import P115Client, check_response

    return P115Client, check_response


def cookies_path():
    config_path = os.path.join(_tool_dir(), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        custom_path = config.get("115_cookies_path")
        if custom_path:
            if os.path.isabs(custom_path):
                return custom_path
            return os.path.abspath(os.path.join(os.path.dirname(config_path), custom_path))
    except Exception:
        pass
    return os.path.join(_tool_dir(), "115-cookies.txt")


def _read_cookie_string():
    path = cookies_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content or None
    except Exception:
        return None


def _cookie_header_to_dict(cookie_header: str):
    items = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k and v:
            items[k] = v
    return items


def _deep_find_first_str(obj, keys):
    if obj is None:
        return ""
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            r = _deep_find_first_str(v, keys)
            if r:
                return r
        return ""
    if isinstance(obj, list):
        for it in obj:
            r = _deep_find_first_str(it, keys)
            if r:
                return r
        return ""
    return ""


def _deep_find_first_int(obj, keys):
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().isdigit():
                try:
                    return int(v.strip())
                except Exception:
                    pass
        for v in obj.values():
            r = _deep_find_first_int(v, keys)
            if r is not None:
                return r
        return None
    if isinstance(obj, list):
        for it in obj:
            r = _deep_find_first_int(it, keys)
            if r is not None:
                return r
        return None
    return None


def load_client():
    path = cookies_path()
    if os.path.isfile(path):
        P115Client, _ = _import_p115client()
        return P115Client(Path(path), check_for_relogin=True)
    return None


def get_login_status():
    import requests
    cookie = _read_cookie_string()
    if not cookie:
        return {"logged_in": False, "user": None, "reason": "cookie文件不存在"}
    try:
        sess = requests.Session()
        sess.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://115.com/",
            "User-Agent": "Mozilla/5.0",
        })

        for k, v in _cookie_header_to_dict(cookie).items():
            sess.cookies.set(k, v, domain=".115.com", path="/")

        sess.get("https://webapi.115.com/", timeout=10)

        index_resp = sess.get("https://webapi.115.com/files/index_info", timeout=10)
        index_resp.raise_for_status()
        index_body = index_resp.json()
        if isinstance(index_body, dict) and index_body.get("state") in (False, 0):
            reason = index_body.get("error") or index_body.get("message") or index_body.get("msg") or str(index_body)
            return {"logged_in": False, "user": None, "reason": f"接口返回失败: {reason}"}

        name_keys = [
            "user_name",
            "nickname",
            "username",
            "name",
            "uname",
            "nick",
        ]
        id_keys = ["user_id", "uid", "userid", "id"]

        user = ""
        user_id = None
        try:
            info_resp = sess.get("https://webapi.115.com/user/info", timeout=10)
            info_resp.raise_for_status()
            body = info_resp.json()
            if isinstance(body, dict) and body.get("state") in (False, 0):
                body = {}
            user = _deep_find_first_str(body, name_keys)
            user_id = _deep_find_first_int(body, id_keys)
        except Exception:
            user = ""
            user_id = None

        if not user:
            user = _deep_find_first_str(index_body, name_keys)
        if user_id is None:
            user_id = _deep_find_first_int(index_body, id_keys)

        if not user:
            try:
                info2 = sess.get("https://webapi.115.com/user/get_info", timeout=10)
                if info2.status_code == 200:
                    body2 = info2.json()
                    user = _deep_find_first_str(body2, name_keys) or user
                    user_id = _deep_find_first_int(body2, id_keys) if user_id is None else user_id
            except Exception:
                pass

        if not user and user_id is not None:
            user = f"UID:{user_id}"

        return {"logged_in": True, "user": user}
    except Exception as e:
        return {"logged_in": False, "user": None, "reason": f"接口调用失败: {e}"}


def qr_login_step1():
    import requests
    resp = requests.get("https://qrcodeapi.115.com/api/1.0/web/1.0/token/")
    data = resp.json()
    data = data.get("data") or data
    uid = data["uid"]
    qrcode_url = f"https://qrcodeapi.115.com/api/1.0/mac/1.0/qrcode?uid={uid}"
    img_resp = requests.get(qrcode_url)
    img_b64 = base64.b64encode(img_resp.content).decode("ascii")
    return {
        "uid": uid,
        "time": data["time"],
        "sign": data["sign"],
        "qrcode_base64": img_b64,
    }


def qr_login_step2(uid, time, sign):
    import requests
    url = "https://qrcodeapi.115.com/get/status/"
    resp = requests.get(url, params={"uid": uid, "time": time, "sign": sign})
    status = resp.json()
    data = status.get("data") or status
    code = data.get("status")
    if code == 2:
        return {"status": 2, "message": "已登录"}
    elif code == 1:
        return {"status": 1, "message": "已扫描"}
    elif code == 0:
        return {"status": 0, "message": "等待扫码"}
    else:
        return {"status": -1, "message": "二维码已过期"}


def qr_login_step3(uid, app="alipaymini"):
    import requests
    try:
        url = f"https://passportapi.115.com/app/1.0/{app}/1.0/login/qrcode/"
        payload = {"app": app, "account": uid}
        resp = requests.post(url, data=payload)
        body = resp.json()
        body_data = body.get("data") or body
        cookie = body_data.get("cookie") or ""
        cookie_header = ""

        if isinstance(cookie, dict):
            cookie_header = "; ".join(f"{k}={v}" for k, v in cookie.items() if v)
        else:
            cookie_dict = resp.cookies.get_dict()
            if cookie_dict:
                cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items() if v)

        if not cookie_header and isinstance(cookie, str):
            cookie_header = cookie

        if cookie_header and isinstance(cookie_header, str):
            path = cookies_path()
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(cookie_header)
            return {"success": True, "message": "登录成功"}

        return {"success": False, "message": "获取 cookie 失败", "raw": str(body)[:200]}
    except Exception as e:
        return {"success": False, "message": str(e)}


def offline_submit(magnet, save_path):
    client = load_client()
    if client is None:
        return {"success": False, "message": "未登录"}
    try:
        _, check_response = _import_p115client()
        cid = 0
        if save_path:
            cid = _resolve_path_to_cid(save_path)
        payload = {"url": magnet}
        if cid:
            payload["wp_path_id"] = cid
        resp = check_response(client.offline_add_url(payload))
        pick_code = None
        if isinstance(resp, dict):
            data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
            pick_code = data.get("pick_code") or data.get("pickcode") or data.get("pc")
        return {"success": True, "pick_code": pick_code, "response": resp}
    except Exception as e:
        return {"success": False, "message": str(e)}


def offline_list():
    client = load_client()
    if client is None:
        return {"success": False, "message": "未登录", "tasks": []}
    try:
        _, check_response = _import_p115client()
        resp = check_response(client.offline_list({"page": 1, "page_size": 100}))
        tasks = []
        if isinstance(resp, dict):
            for k in ("tasks", "data", "list"):
                v = resp.get(k)
                if isinstance(v, list):
                    tasks = v
                    break
                if isinstance(v, dict):
                    for kk in ("tasks", "data", "list"):
                        vv = v.get(kk)
                        if isinstance(vv, list):
                            tasks = vv
                            break
                    if tasks:
                        break
        return {"success": True, "tasks": tasks, "response": resp}
    except Exception as e:
        return {"success": False, "message": str(e), "tasks": []}


def _resolve_path_to_cid(path):
    client = load_client()
    if client is None:
        return 0
    try:
        _, check_response = _import_p115client()
        resp = check_response(client.fs_dir_getid(path))
        if not isinstance(resp, dict):
            return 0
        data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
        cid = data.get("id") or data.get("file_id") or data.get("cid") or 0
        try:
            return int(cid)
        except Exception:
            return 0
    except Exception:
        return 0


def search_files(keyword, cid=0):
    client = load_client()
    if client is None:
        return []
    try:
        _, check_response = _import_p115client()
        resp = check_response(client.fs_search(keyword, cid=cid))
        data = resp if isinstance(resp, list) else resp.get("data", []) if isinstance(resp, dict) else []
        return data
    except Exception:
        return []


def _get_default_save_path():
    config_path = os.path.join(_tool_dir(), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("115_save_path", "")
    except Exception:
        return ""


def parse_magnet_simple(magnet):
    if not magnet or not magnet.startswith("magnet:?"):
        return {"ok": False, "errors": ["not_magnet"]}
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(magnet)
    qs = parse_qs(parsed.query)
    xt = (qs.get("xt") or [None])[0]
    dn = (qs.get("dn") or [None])[0]
    btih = None
    infohash_hex = None
    if xt:
        m = re.search(r"urn:btih:([A-Za-z0-9]+)", xt)
        if m:
            btih = m.group(1)
    if btih:
        if re.fullmatch(r"[A-Fa-f0-9]{40}", btih):
            infohash_hex = btih.lower()
        else:
            try:
                raw = base64.b32decode(btih.upper() + "====", casefold=True)
                if len(raw) == 20:
                    infohash_hex = raw.hex()
            except Exception:
                pass
    errors = []
    if not btih:
        errors.append("missing_btih")
    if btih and not infohash_hex:
        errors.append("invalid_btih")
    return {"ok": len(errors) == 0, "btih": btih, "dn": dn, "infohash_hex": infohash_hex, "errors": errors}


def _normalize_for_comparison(name):
    name = name.strip()
    first_bracket = name.find('[')
    if first_bracket > 0:
        name = name[first_bracket:]
    name = re.split(r'\s*\+\s*', name)[0]
    name = name.lower()
    name = name.replace('・', '').replace('♡', '').replace('❤', '').replace('♥', '')
    name = name.replace('~', '').replace('～', '').replace('〜', '').replace('！', '').replace('：', '')
    name = re.sub(r'\(\s*mdf\s*\+\s*mds\s*\)', '', name)
    name = re.sub(r'\(\s*mdf\+mds\s*\)', '', name)
    name = re.sub(r'\b(disc|disk)\s*\d+\b', '', name)
    name = re.sub(r'\[(\d{7,})\]', '', name)
    name = re.sub(r'\[\s*\d[\d.,]*(?:\.\d+)?\s*(?:k|m|g|t)i?b\s*\]', '', name)
    name = re.sub(r'\b(?:crack|patch|update)\b', '', name)
    name = re.sub(r'(?:パッケージ版|ダウンロード版|dl\s*版|通常版)', '', name)
    name = re.sub(r'\bmini\s*adv\b', '', name)
    name = re.sub(r'[\s\-_.]+', ' ', name).strip()
    return name


def _search_keyword_from_dn(dn):
    name = dn.strip()
    first_bracket = name.find('[')
    if first_bracket > 0:
        name = name[first_bracket:]
    brackets = re.findall(r'\[([^\]]+)\]', name)
    date_bracket = ""
    rest = []
    for raw in brackets:
        v = raw.strip()
        if re.fullmatch(r'\d{6}', v):
            if not date_bracket:
                date_bracket = v
            continue
        if re.fullmatch(r'\d{7,}', v):
            continue
        if re.fullmatch(r'\s*\d[\d.,]*(?:\.\d+)?\s*(?:k|m|g|t)i?b\s*', v.lower()):
            continue
        rest.append(v)
    if date_bracket and rest:
        return f"[{date_bracket}] [{rest[0]}]"
    if date_bracket:
        return f"[{date_bracket}]"
    if rest:
        return f"[{rest[0]}]"
    name = re.split(r'\s*\+\s*', name)[0]
    return name.strip()[:30]


def check_magnet_exists(magnet, save_path, debug=False):
    parsed = parse_magnet_simple(magnet)
    if not parsed.get("ok"):
        out = {
            "exists": False,
            "confidence": "none",
            "infohash_hex": parsed.get("infohash_hex"),
            "matched_files": [],
            "in_offline_tasks": False,
            "message": "磁链解析失败: " + ", ".join(parsed.get("errors", [])),
        }
        if debug:
            out["debug"] = {"stage": "parse_magnet_simple", "parsed": parsed}
        return out

    infohash_hex = parsed.get("infohash_hex")
    dn = parsed.get("dn", "")

    matched_files = []
    in_offline = False
    confidence = "none"

    dbg = None
    if debug:
        dbg = {
            "parsed": parsed,
            "has_cookie_file": bool(_read_cookie_string()),
            "save_path_input": save_path,
            "save_path_default": _get_default_save_path(),
            "steps": [],
        }

    ol = offline_list()
    if ol.get("success"):
        tasks = ol.get("tasks", [])
        if isinstance(tasks, dict):
            tasks = tasks.get("data", []) if isinstance(tasks, dict) else []
        for task in tasks:
            if isinstance(task, dict):
                task_url = (task.get("url") or "").lower()
                if magnet.lower() in task_url or (infohash_hex and infohash_hex in task_url):
                    in_offline = True
                    break
        if dbg is not None:
            dbg["steps"].append({"stage": "offline_list", "success": True, "tasks_len": len(tasks), "in_offline": in_offline})
    else:
        if dbg is not None:
            dbg["steps"].append({"stage": "offline_list", "success": False, "message": ol.get("message")})

    if dn:
        actual_save_path = save_path or _get_default_save_path()
        cid = _resolve_path_to_cid(actual_save_path) if actual_save_path else 0
        keyword = _search_keyword_from_dn(dn)
        keywords = []
        if keyword:
            keywords.append(keyword)
            compact = keyword.replace("] [", "][")
            if compact != keyword:
                keywords.append(compact)
            compact2 = re.sub(r'\s+', ' ', keyword).strip()
            if compact2 and compact2 not in keywords:
                keywords.append(compact2)
        norm_dn = _normalize_for_comparison(dn)
        if dbg is not None:
            dbg["dn"] = dn
            dbg["norm_dn"] = norm_dn
            dbg["actual_save_path"] = actual_save_path
            dbg["cid"] = cid
            dbg["keyword_primary"] = keyword
            dbg["keywords"] = keywords

        if cid is not None:
            files = []
            for kw in keywords:
                files = search_files(kw, cid)
                if dbg is not None:
                    dbg["steps"].append({"stage": "search_files", "mode": "keyword", "query": kw, "cid": cid, "result_len": len(files)})
                for f in files:
                    fname = f.get("n", "") if isinstance(f, dict) else ""
                    norm_fname = _normalize_for_comparison(fname)
                    if norm_dn and norm_fname and (norm_dn in norm_fname or norm_fname in norm_dn):
                        matched_files.append({
                            "name": fname,
                            "size": f.get("s") if isinstance(f, dict) else None,
                            "pick_code": f.get("pc") if isinstance(f, dict) else None,
                        })
                if dbg is not None:
                    dbg["steps"].append({
                        "stage": "match_compare",
                        "mode": "keyword",
                        "query": kw,
                        "matched_len": len(matched_files),
                        "sample_files": [
                            {"name": (it.get("n") or ""), "norm": _normalize_for_comparison(it.get("n") or "")}
                            for it in (files[:5] if isinstance(files, list) else [])
                            if isinstance(it, dict)
                        ],
                    })
                if matched_files:
                    break

            if not files and not matched_files:
                brackets = re.findall(r'\[(\d{6})\]', dn)
                date_code = brackets[0] if brackets else ""
                if date_code:
                    for kw in (date_code, f"[{date_code}]"):
                        files = search_files(kw, cid or 0)
                        if dbg is not None:
                            dbg["steps"].append({"stage": "search_files", "mode": "date_code", "query": kw, "cid": cid or 0, "result_len": len(files)})
                        for f in files:
                            fname = f.get("n", "") if isinstance(f, dict) else ""
                            norm_fname = _normalize_for_comparison(fname)
                            if norm_dn and norm_fname and (norm_dn in norm_fname or norm_fname in norm_dn):
                                matched_files.append({
                                    "name": fname,
                                    "size": f.get("s") if isinstance(f, dict) else None,
                                    "pick_code": f.get("pc") if isinstance(f, dict) else None,
                                })
                        if dbg is not None:
                            dbg["steps"].append({
                                "stage": "match_compare",
                                "mode": "date_code",
                                "query": kw,
                                "matched_len": len(matched_files),
                                "sample_files": [
                                    {"name": (it.get("n") or ""), "norm": _normalize_for_comparison(it.get("n") or "")}
                                    for it in (files[:5] if isinstance(files, list) else [])
                                    if isinstance(it, dict)
                                ],
                            })
                        if matched_files:
                            break

            if not files and not matched_files:
                name_no_bracket = re.split(r'\s*\+\s*', dn)[0]
                first_bracket = name_no_bracket.find('[')
                if first_bracket > 0:
                    name_no_bracket = name_no_bracket[first_bracket:]
                name_no_bracket = re.sub(r'\[[^\]]+\]', '', name_no_bracket).strip()
                if len(name_no_bracket) >= 3:
                    files = search_files(name_no_bracket[:20], cid or 0)
                    if dbg is not None:
                        dbg["steps"].append({"stage": "search_files", "mode": "plain_name", "query": name_no_bracket[:20], "cid": cid or 0, "result_len": len(files)})
                    for f in files:
                        fname = f.get("n", "") if isinstance(f, dict) else ""
                        norm_fname = _normalize_for_comparison(fname)
                        if norm_dn and norm_fname and (norm_dn in norm_fname or norm_fname in norm_dn):
                            matched_files.append({
                                "name": fname,
                                "size": f.get("s") if isinstance(f, dict) else None,
                                "pick_code": f.get("pc") if isinstance(f, dict) else None,
                            })
                    if dbg is not None:
                        dbg["steps"].append({
                            "stage": "match_compare",
                            "mode": "plain_name",
                            "query": name_no_bracket[:20],
                            "matched_len": len(matched_files),
                            "sample_files": [
                                {"name": (it.get("n") or ""), "norm": _normalize_for_comparison(it.get("n") or "")}
                                for it in (files[:5] if isinstance(files, list) else [])
                                if isinstance(it, dict)
                            ],
                        })

        if not matched_files and not in_offline and infohash_hex:
            try:
                broad = search_files(infohash_hex[:12], cid or 0)
                if dbg is not None:
                    dbg["steps"].append({"stage": "search_files", "mode": "infohash_prefix", "query": infohash_hex[:12], "cid": cid or 0, "result_len": len(broad)})
                for f in broad:
                    fname = f.get("n", "") if isinstance(f, dict) else ""
                    norm_fname = _normalize_for_comparison(fname)
                    if norm_dn and norm_fname and (norm_dn in norm_fname or norm_fname in norm_dn):
                        matched_files.append({
                            "name": fname,
                            "size": f.get("s") if isinstance(f, dict) else None,
                            "pick_code": f.get("pc") if isinstance(f, dict) else None,
                        })
            except Exception:
                pass

    if matched_files:
        confidence = "high"
    elif in_offline:
        confidence = "high"
    elif dn and cid:
        keyword = _search_keyword_from_dn(dn)
        broad_kw = keyword[:max(8, len(keyword)//3)] if keyword else ""
        broad_kw2 = broad_kw.replace("] [", "][") if broad_kw else ""
        broad = search_files(broad_kw, cid) if broad_kw else []
        if not broad and broad_kw2 and broad_kw2 != broad_kw:
            broad = search_files(broad_kw2, cid)
        if broad:
            confidence = "low"

    out = {
        "exists": bool(matched_files) or in_offline,
        "confidence": confidence,
        "infohash_hex": infohash_hex,
        "matched_files": matched_files,
        "in_offline_tasks": in_offline,
        "dn": dn,
    }
    if dbg is not None:
        dbg["result"] = {"matched_len": len(matched_files), "in_offline": in_offline, "confidence": confidence}
        out["debug"] = dbg
    if dbg is not None and not dbg.get("has_cookie_file"):
        out["message"] = "cookie文件不存在或为空，可能未登录导致搜索结果为空"
    return out
