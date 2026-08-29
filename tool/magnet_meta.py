import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse


def bdecode(data, idx=0):
    lead = data[idx : idx + 1]
    if lead == b"i":
        end = data.index(b"e", idx)
        num = int(data[idx + 1 : end])
        return num, end + 1
    if lead == b"l":
        idx += 1
        out = []
        while data[idx : idx + 1] != b"e":
            v, idx = bdecode(data, idx)
            out.append(v)
        return out, idx + 1
    if lead == b"d":
        idx += 1
        out = {}
        while data[idx : idx + 1] != b"e":
            k, idx = bdecode(data, idx)
            v, idx = bdecode(data, idx)
            out[k] = v
        return out, idx + 1
    if lead.isdigit():
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        start = colon + 1
        end = start + length
        return data[start:end], end
    raise ValueError("invalid bencode")


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, str):
        b = value.encode("utf-8")
        return str(len(b)).encode("ascii") + b":" + b
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = []
        for k in sorted(value.keys()):
            items.append(bencode(k))
            items.append(bencode(value[k]))
        return b"d" + b"".join(items) + b"e"
    raise TypeError("unsupported type")


def bytes_to_str(b):
    if b is None:
        return None
    if isinstance(b, bytes):
        return b.decode("utf-8", errors="replace")
    return str(b)


def parse_magnet(magnet):
    if not magnet or not magnet.startswith("magnet:?"):
        return {"ok": False, "errors": ["not_magnet"]}

    parsed = urlparse(magnet)
    qs = parse_qs(parsed.query)
    xt = (qs.get("xt") or [None])[0]
    dn = (qs.get("dn") or [None])[0]
    trackers = qs.get("tr") or []

    btih = None
    infohash_hex = None
    infohash_base32 = None
    if xt:
        m = re.search(r"urn:btih:([A-Za-z0-9]+)", xt)
        if m:
            btih = m.group(1)

    if btih:
        if re.fullmatch(r"[A-Fa-f0-9]{40}", btih):
            infohash_hex = btih.lower()
            infohash_base32 = base64.b32encode(bytes.fromhex(infohash_hex)).decode("ascii").lower().strip("=")
        else:
            try:
                raw = base64.b32decode(btih.upper() + "====", casefold=True)
                if len(raw) == 20:
                    infohash_hex = raw.hex()
                    infohash_base32 = btih.lower()
            except Exception:
                pass

    errors = []
    if not btih:
        errors.append("missing_btih")
    if btih and not infohash_hex:
        errors.append("invalid_btih")

    return {
        "ok": len(errors) == 0,
        "btih": btih,
        "dn": dn,
        "trackers": trackers,
        "infohash_hex": infohash_hex,
        "infohash_base32": infohash_base32,
        "errors": errors,
    }


def find_torrent_file(dir_path):
    for name in os.listdir(dir_path):
        if name.endswith(".torrent"):
            return os.path.join(dir_path, name)
    return None


def parse_torrent(torrent_path):
    data = open(torrent_path, "rb").read()
    meta, _ = bdecode(data, 0)
    info = meta.get(b"info") if isinstance(meta, dict) else None
    if not isinstance(info, dict):
        raise ValueError("missing_info")

    infohash = hashlib.sha1(bencode(info)).hexdigest()
    name = bytes_to_str(info.get(b"name"))

    files = []
    total = 0
    if b"files" in info:
        for f in info.get(b"files") or []:
            if not isinstance(f, dict):
                continue
            length = int(f.get(b"length") or 0)
            path_parts = f.get(b"path") or []
            path = "/".join(bytes_to_str(p) for p in path_parts)
            files.append({"path": path, "size": length})
            total += length
    else:
        length = int(info.get(b"length") or 0)
        files.append({"path": name or "", "size": length})
        total = length

    return {"infohash_hex": infohash, "name": name, "total_size": total, "files": files}


def fetch_metadata_with_aria2(magnet, timeout_sec):
    aria2 = shutil.which("aria2c")
    if not aria2:
        return None, {"errors": ["aria2c_not_found"]}

    with tempfile.TemporaryDirectory(prefix="magnet-meta-") as tmpdir:
        cmd = [
            aria2,
            "--bt-metadata-only=true",
            "--bt-save-metadata=true",
            "--seed-time=0",
            "--summary-interval=0",
            "--console-log-level=warn",
            "--dir",
            tmpdir,
            magnet,
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            return None, {"errors": ["metadata_timeout"]}

        torrent_path = find_torrent_file(tmpdir)
        if not torrent_path:
            err = (p.stderr or p.stdout or "").strip()
            return None, {"errors": ["metadata_failed"], "detail": err[:2000]}

        try:
            parsed = parse_torrent(torrent_path)
            return parsed, {"errors": []}
        except Exception as e:
            return None, {"errors": ["torrent_parse_failed"], "detail": str(e)}


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    magnet = payload.get("magnet") or ""
    timeout_sec = int(payload.get("timeout_sec") or 20)

    base = parse_magnet(magnet)
    result = {
        "ok": base.get("ok", False),
        "magnet": magnet,
        "btih": base.get("btih"),
        "dn": base.get("dn"),
        "trackers": base.get("trackers") or [],
        "infohash_hex": base.get("infohash_hex"),
        "infohash_base32": base.get("infohash_base32"),
        "errors": base.get("errors") or [],
        "metadata": None,
        "metadata_errors": [],
        "metadata_detail": None,
    }

    if base.get("infohash_hex"):
        meta, meta_state = fetch_metadata_with_aria2(magnet, timeout_sec)
        result["metadata"] = meta
        result["metadata_errors"] = meta_state.get("errors") or []
        result["metadata_detail"] = meta_state.get("detail")
        if meta and meta.get("infohash_hex") and meta.get("infohash_hex") != base.get("infohash_hex"):
            result["errors"] = list(set(result["errors"] + ["infohash_mismatch"]))

    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

