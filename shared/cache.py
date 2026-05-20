"""キャッシュ保存先の解決と TTL 付き JSON read/write。

exe 配置フォルダ下 `cache/` を第一候補、書き込み不可なら
`%LOCALAPPDATA%\\PkgUpdater\\cache\\` にフォールバック。
NodeUpdater / PypkgUpdater いずれも同じディレクトリを共有する (キーで分離)。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path


def _exe_dir() -> Path:
    # PyInstaller onefile では sys.executable が実 exe パス（__file__ は temp 展開先）
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def root_dir() -> Path:
    """書き込み可能なルートディレクトリ。

    第一候補: exe 配置フォルダ（dev ではリポジトリルート）
    フォールバック: %LOCALAPPDATA%\\PkgUpdater\\

    cache や state ファイルの親として共通利用する。
    """
    primary = _exe_dir()
    try:
        primary.mkdir(exist_ok=True)
        probe = primary / '.write_test'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        return primary
    except (OSError, PermissionError):
        fallback = Path(os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))) / 'PkgUpdater'
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def cache_dir() -> Path:
    d = root_dir() / 'cache'
    d.mkdir(exist_ok=True)
    return d


_SAFE_KEY = re.compile(r'[^A-Za-z0-9._-]+')


def _key_to_filename(key: str) -> str:
    return _SAFE_KEY.sub('_', key)[:120] + '.json'


def load(key: str, ttl_seconds: int, invalidate_if_newer: Path | None = None) -> dict | None:
    """TTL 内なら data を返す。期限切れ・無し・破損なら None。

    invalidate_if_newer に Path を渡すと、そのファイルの mtime がキャッシュ
    生成時刻より新しい場合も失効扱いにする (lockfile が更新されたら再スキャン
    したいケース向け)。
    """
    file = cache_dir() / _key_to_filename(key)
    try:
        obj = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    cached_at = obj.get('cachedAt', 0) / 1000.0  # ms → s
    if time.time() - cached_at >= ttl_seconds:
        return None
    if invalidate_if_newer is not None:
        try:
            if invalidate_if_newer.stat().st_mtime > cached_at:
                return None
        except OSError:
            pass
    return obj.get('data')


def save(key: str, data: dict) -> None:
    file = cache_dir() / _key_to_filename(key)
    payload = {'cachedAt': int(time.time() * 1000), 'data': data}
    file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
