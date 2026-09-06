"""下载并解析 sukebei nyaa 的 .torrent，提取与 115 离线产物精确对应的 info 信息。

115 的离线任务/下载目录名来自种子文件的 info.name（不是磁链 dn）。
本模块在选中候选时下载 .torrent、解析出真实目录名与文件清单，供后续整理精确定位。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from urllib.parse import urlparse

SUKEBEI_BASE = "https://sukebei.nyaa.si"
REQUEST_TIMEOUT = 25

logger = logging.getLogger(__name__)


def bdecode(data: bytes):
    """极简 bencode 解码，返回 (list/dict/bytes/int)。"""
    def dec(i):
        c = data[i:i + 1]
        if c == b'i':
            j = data.index(b'e', i)
            return int(data[i + 1:j]), j + 1
        if c == b'l':
            i += 1
            out = []
            while data[i:i + 1] != b'e':
                v, i = dec(i)
                out.append(v)
            return out, i + 1
        if c == b'd':
            i += 1
            out = {}
            while data[i:i + 1] != b'e':
                k, i = dec(i)
                v, i = dec(i)
                out[k] = v
            return out, i + 1
        j = data.index(b':', i)
        n = int(data[i:j])
        s = data[j + 1:j + 1 + n]
        return s, j + 1 + n
    val, _ = dec(0)
    return val


def bencode(v):
    """bencode 编码（dict 自动按键排序），用于重算 info_hash。"""
    if isinstance(v, int):
        return b'i%de' % v
    if isinstance(v, bytes):
        return b'%d:%s' % (len(v), v)
    if isinstance(v, list):
        return b'l' + b''.join(bencode(x) for x in v) + b'e'
    if isinstance(v, dict):
        return b'd' + b''.join(bencode(k) + bencode(x) for k, x in sorted(v.items())) + b'e'
    raise TypeError(f"cannot bencode {type(v)}")


def _decode_bytes(b):
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return str(b or "")


def parse_torrent(data: bytes, expected_infohash: str | None = None) -> dict | None:
    """解析 .torrent，返回 info 元数据；可选校验 infohash 防止错种。"""
    try:
        torrent = bdecode(data)
    except Exception:
        logger.debug("bencode 解析失败: %d bytes", len(data), exc_info=True)
        return None
    info = torrent.get(b"info")
    if not isinstance(info, dict):
        return None

    # 兼容 name.utf-8 / path.utf-8 覆盖
    name_key = b"name.utf-8" if b"name.utf-8" in info else b"name"
    raw_name = info.get(name_key)
    if not isinstance(raw_name, bytes):
        return None
    name = _decode_bytes(raw_name)

    files = []
    total_size = info.get(b"length", 0)
    if isinstance(info.get(b"files"), list):
        total_size = 0
        for f in info[b"files"]:
            if not isinstance(f, dict):
                continue
            path_key = b"path.utf-8" if b"path.utf-8" in f else b"path"
            raw_path = f.get(path_key)
            parts = [_decode_bytes(p) for p in raw_path] if isinstance(raw_path, list) else []
            length = f.get(b"length", 0)
            total_size += length if isinstance(length, int) else 0
            files.append({"path": "/".join(parts), "length": length if isinstance(length, int) else 0})

    infohash_hex = None
    try:
        infohash_hex = hashlib.sha1(bencode(info)).hexdigest()
    except Exception:
        logger.debug("info_hash 重算失败", exc_info=True)
        infohash_hex = None

    if expected_infohash and infohash_hex:
        if str(infohash_hex).lower() != str(expected_infohash).lower():
            logger.warning("torrent infohash 不匹配: %s != %s", infohash_hex, expected_infohash)
            return None

    return {
        "name": name,
        "infohash_hex": infohash_hex,
        "files": files,
        "total_size": total_size,
    }


def torrent_id_from_url(view_url: str | None) -> str | None:
    if not view_url:
        return None
    m = re.search(r"/(?:view|download)/(\d+)", view_url)
    if m:
        return m.group(1)
    # https://sukebei.nyaa.si/download/123.torrent
    m = re.search(r"/download/(\d+)\.torrent", view_url)
    return m.group(1) if m else None


def fetch_torrent_meta(session, view_url: str | None, expected_infohash: str | None = None,
                       pacer=None, timeout: int = REQUEST_TIMEOUT) -> dict | None:
    """从 sukebei 下载 .torrent 并解析 info 元数据；任何失败返回 None（调用方降级）。"""
    tid = torrent_id_from_url(view_url)
    if not tid:
        return None
    url = f"{SUKEBEI_BASE}/download/{tid}.torrent"
    if pacer is not None:
        pacer.before_request()
    started = time.monotonic()
    try:
        resp = session.get(url, timeout=timeout)
    finally:
        if pacer is not None:
            pacer.metrics["network_seconds"] += time.monotonic() - started
    if pacer is not None:
        pacer.check_stop()
    if resp.status_code == 429:
        logger.warning("sukebei torrent 429: %s", url)
        return None
    if resp.status_code != 200 or not resp.content:
        logger.debug("sukebei torrent download failed: %s status=%s", url, resp.status_code)
        return None
    meta = parse_torrent(resp.content, expected_infohash=expected_infohash)
    if meta is None:
        return None
    logger.info("TORRENT_META %s | info.name=%s", expected_infohash or "", meta["name"][:60])
    return meta


def meta_to_json(meta: dict | None) -> str | None:
    if not meta:
        return None
    return json.dumps({"name": meta.get("name"), "files": meta.get("files"),
                       "total_size": meta.get("total_size")}, ensure_ascii=False)
