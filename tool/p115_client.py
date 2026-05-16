import base64
import json
import os
import re
import sys
from pathlib import Path

from p115client import P115Client, check_response


def _tool_dir():
    return os.path.dirname(os.path.abspath(__file__))


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


def load_client():
    path = cookies_path()
    if os.path.isfile(path):
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

        user = ""
        try:
            info_resp = sess.get("https://webapi.115.com/user/info", timeout=10)
            info_resp.raise_for_status()
            body = info_resp.json()
            if isinstance(body, dict) and body.get("state") in (False, 0):
                body = {}
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                user = data.get("user_name") or data.get("nickname") or data.get("username") or data.get("name") or ""
        except Exception:
            user = ""

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
    name = name.replace('~', '').replace('～', '').replace('！', '').replace('：', '')
    name = re.sub(r'[\s\-_.]+', ' ', name).strip()
    return name


def _search_keyword_from_dn(dn):
    name = dn.strip()
    first_bracket = name.find('[')
    if first_bracket > 0:
        name = name[first_bracket:]
    name = re.split(r'\s*\+\s*', name)[0]
    return name.strip()[:80]


def check_magnet_exists(magnet, save_path):
    parsed = parse_magnet_simple(magnet)
    if not parsed.get("ok"):
        return {
            "exists": False,
            "confidence": "none",
            "infohash_hex": parsed.get("infohash_hex"),
            "matched_files": [],
            "in_offline_tasks": False,
            "message": "磁链解析失败: " + ", ".join(parsed.get("errors", [])),
        }

    infohash_hex = parsed.get("infohash_hex")
    dn = parsed.get("dn", "")

    matched_files = []
    in_offline = False
    confidence = "none"

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

    if dn:
        actual_save_path = save_path or _get_default_save_path()
        cid = _resolve_path_to_cid(actual_save_path) if actual_save_path else 0
        keyword = _search_keyword_from_dn(dn)
        if cid is not None:
            files = search_files(keyword, cid)
            norm_dn = _normalize_for_comparison(dn)
            for f in files:
                fname = f.get("n", "") if isinstance(f, dict) else ""
                norm_fname = _normalize_for_comparison(fname)
                if norm_dn and norm_fname and (norm_dn in norm_fname or norm_fname in norm_dn):
                    matched_files.append({
                        "name": fname,
                        "size": f.get("s") if isinstance(f, dict) else None,
                        "pick_code": f.get("pc") if isinstance(f, dict) else None,
                    })

        if not matched_files and not in_offline and infohash_hex:
            try:
                broad = search_files(infohash_hex[:12], cid or 0)
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
        broad = search_files(keyword[:max(8, len(keyword)//3)], cid)
        if broad:
            confidence = "low"

    return {
        "exists": bool(matched_files) or in_offline,
        "confidence": confidence,
        "infohash_hex": infohash_hex,
        "matched_files": matched_files,
        "in_offline_tasks": in_offline,
        "dn": dn,
    }
