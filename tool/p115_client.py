import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import urlopen

from p115client import P115Client, check_response, tool


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


def load_client():
    path = cookies_path()
    if os.path.isfile(path):
        return P115Client(Path(path), check_for_relogin=True)
    return None


def get_login_status():
    client = load_client()
    if client is None:
        return {"logged_in": False, "user": None}
    try:
        resp = check_response(client.fs_get_user_info())
        user = {}
        if isinstance(resp, dict):
            data = resp.get("data") or resp
            if isinstance(data, dict):
                user = data
        return {"logged_in": True, "user": user.get("user_name", user.get("nickname", ""))}
    except Exception:
        return {"logged_in": False, "user": None}


def qr_login_step1():
    token = tool.get_qrcode_token()
    uid = token["uid"]

    img_data = urlopen(f"https://qrcodeapi.115.com/api/1.0/mac/1.0/qrcode?uid={uid}").read()
    img_b64 = base64.b64encode(img_data).decode("ascii")

    return {
        "uid": uid,
        "time": token["time"],
        "sign": token["sign"],
        "qrcode_base64": img_b64,
    }


def qr_login_step2(uid, time, sign):
    payload = {"uid": uid, "time": time, "sign": sign}
    status = tool.get_qrcode_status(payload)
    code = status.get("status")
    if code == 2:
        return {"status": 2, "message": "已登录"}
    elif code == 1:
        return {"status": 1, "message": "已扫描"}
    elif code == 0:
        return {"status": 0, "message": "等待扫码"}
    else:
        return {"status": -1, "message": "二维码已过期"}


def qr_login_step3(uid, app="alipaymini"):
    try:
        result = tool.post_qrcode_result(uid, app)
        data = result.get("data") or result
        cookie = data.get("cookie")
        if not cookie:
            for v in data.values():
                if isinstance(v, str) and "UID=" in v:
                    cookie = v
                    break
        if cookie:
            path = cookies_path()
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(cookie)
            return {"success": True, "message": "登录成功"}
        return {"success": False, "message": "获取 cookie 失败", "raw": str(result)[:200]}
    except Exception as e:
        return {"success": False, "message": str(e)}


def offline_submit(magnet, save_path):
    client = load_client()
    if client is None:
        return {"success": False, "message": "未登录"}
    try:
        resp = check_response(client.offline_download_add(magnet, save_path=save_path))
        return {"success": True, "pick_code": resp.get("pick_code")}
    except Exception as e:
        return {"success": False, "message": str(e)}


def offline_list():
    client = load_client()
    if client is None:
        return {"success": False, "message": "未登录", "tasks": []}
    try:
        resp = check_response(client.offline_download_list())
        data = resp if isinstance(resp, list) else resp.get("data", []) if isinstance(resp, dict) else []
        return {"success": True, "tasks": data}
    except Exception as e:
        return {"success": False, "message": str(e), "tasks": []}


def _resolve_path_to_cid(path):
    client = load_client()
    if client is None:
        return 0
    try:
        parts = [p for p in path.strip("/").split("/") if p]
        cid = 0
        for part in parts:
            resp = check_response(client.fs_get_id_by_path(part, cid=cid))
            if isinstance(resp, dict):
                cid = resp.get("id", resp.get("cid", 0))
                if not cid:
                    return 0
        return cid
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
        cid = _resolve_path_to_cid(save_path) if save_path else 0
        if cid is not None:
            files = search_files(dn, cid)
            for f in files:
                fname = f.get("n", "") if isinstance(f, dict) else ""
                if dn.lower() in fname.lower():
                    matched_files.append({
                        "name": fname,
                        "size": f.get("s") if isinstance(f, dict) else None,
                        "pick_code": f.get("pc") if isinstance(f, dict) else None,
                    })

    if matched_files:
        confidence = "high"
    elif in_offline:
        confidence = "high"
    elif dn and cid:
        broad = search_files(dn[:max(4, len(dn)//2)], cid)
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
