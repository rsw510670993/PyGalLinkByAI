"""封面永久压缩：把 thumbnails 下的原图转成两档 webp，并可删除原图。

- 主档（lightbox 用）：宽 280 的 webp，quality 82
- 小档（列表卡片用）：宽 96 的 webp，quality 78

原图在 Getchu / DLsite 都能随时重新下载，服务器空间紧张时允许删除。
转换是幂等的：webp 已存在就跳过；删除原图只在对应 webp 写成功之后发生。
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from .egs_thumb import thumb_dir
from .runtime import now_ts, repo_root, write_json_atomic

MAIN_WIDTH = 384
MAIN_QUALITY = 82
STATE_NAME = 'thumbs_optimize_state.json'


def _rel(src: Path) -> Path:
    return src.relative_to(thumb_dir())


def web_path(src: Path, small: bool = False) -> Path:
    rel = _rel(src).with_suffix('.webp')
    sub = Path('web') / 'small' if small else Path('web')
    return thumb_dir() / sub / rel


def source_files() -> list[Path]:
    root = thumb_dir()
    files = sorted(root.glob('*.jpg')) + sorted((root / 'dlsite').glob('*.jpg'))
    return [f for f in files if f.is_file()]


def pending(files: list[Path], limit: int = 0, force: bool = False) -> list[Path]:
    out = files if force else [f for f in files if not web_path(f).is_file()]
    return out[:limit] if limit else out


def convert(src: Path) -> tuple[bool, str]:
    try:
        im = Image.open(src).convert('RGB')
    except (OSError, ValueError) as exc:
        return False, f'打开失败: {exc}'
    width, height = im.size
    try:
        nh = max(1, round(height * MAIN_WIDTH / width))
        out = im if width <= MAIN_WIDTH else im.resize((MAIN_WIDTH, nh), Image.LANCZOS)
        target = web_path(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + '.part')
        out.save(tmp, 'WEBP', quality=MAIN_QUALITY, method=6)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        tmp.replace(target)
    except OSError as exc:
        return False, f'写入失败: {exc}'
    return True, f'{width}x{height}'


def run_optimize(delete_originals: bool = False, limit: int = 0, force: bool = False,
                 should_stop=None, logger=None) -> dict:
    log = logger or (lambda msg: None)
    files = source_files()
    todo = pending(files, limit, force=force)
    stats = {
        'status': 'success',
        'mode': 'optimize',
        'scanned': len(files),
        'total': len(todo),
        'ok': 0,
        'failed': 0,
        'failed_files': [],
        'deleted': 0,
        'delete_originals': delete_originals,
        'force': force,
        'started_at': now_ts(),
        'finished_at': None,
        'last_error': '',
    }
    log(f'待转换 {len(todo)} 张（共 {len(files)} 张原图）')
    for i, src in enumerate(todo):
        if should_stop and should_stop():
            stats['status'] = 'stopped'
            break
        ok, message = convert(src)
        if ok:
            stats['ok'] += 1
            if delete_originals:
                try:
                    src.unlink()
                    stats['deleted'] += 1
                except OSError as exc:
                    stats['last_error'] = f'删除失败 {src.name}: {exc}'
            log(f'[{i + 1}/{len(todo)}] {src.name} ✓ {message}')
        else:
            stats['failed'] += 1
            stats['failed_files'].append(src.name)
            stats['last_error'] = message
            log(f'[{i + 1}/{len(todo)}] {src.name} ✗ {message}')

    if delete_originals:
        # 删除所有「两档 webp 都已存在」的原图（含本轮之前已转换的）。
        for src in source_files():
            if web_path(src).is_file():
                try:
                    src.unlink()
                    stats['deleted'] += 1
                except OSError as exc:
                    stats['last_error'] = f'删除失败 {src.name}: {exc}'

    stats['finished_at'] = now_ts()
    try:
        root = Path(repo_root()) / 'status'
        root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(root / STATE_NAME), stats)
    except OSError:
        pass
    return stats
