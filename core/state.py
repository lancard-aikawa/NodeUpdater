"""永続ユーザー状態の保存（recent_projects など）。

cache とは別扱い: 期限なし、定期削除されない。
保存先: <exe フォルダ>/state.json または %LOCALAPPDATA%\\NodeUpdater\\state.json
"""
from __future__ import annotations

import json
from pathlib import Path

from . import cache

_STATE_FILE = 'state.json'
_MAX_RECENT = 10
_DEFAULT_COOLDOWN_DAYS = 7  # 供給チェーン攻撃対策バッファ (uv/pip のグローバル方針と整合)

DEFAULT_REGISTRY_URL = 'https://registry.npmjs.org'
DEFAULT_OSV_API_URL = 'https://api.osv.dev/v1/querybatch'
DEFAULT_PARALLEL_REQUESTS = 8


def _file() -> Path:
    return cache.root_dir() / _STATE_FILE


def _load_all() -> dict:
    try:
        return json.loads(_file().read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_all(data: dict) -> None:
    try:
        _file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass  # 書けないなら諦める（次回履歴が消えるだけ）


def load_recent_projects() -> list[str]:
    items = _load_all().get('recent_projects') or []
    # 存在しなくなったパスは除外しておく
    return [p for p in items if isinstance(p, str) and Path(p).is_dir()]


def add_recent_project(path: str) -> list[str]:
    """先頭に追加して重複除去、存在しないパスを掃除、上限まで切り詰める。"""
    path = str(Path(path).resolve())
    data = _load_all()
    items = data.get('recent_projects') or []
    # 既存リストから当該パスと存在しないパスを除外
    items = [p for p in items if p != path and isinstance(p, str) and Path(p).is_dir()]
    items.insert(0, path)
    items = items[:_MAX_RECENT]
    data['recent_projects'] = items
    _save_all(data)
    return items


def remove_recent_project(path: str) -> list[str]:
    path = str(Path(path).resolve())
    data = _load_all()
    items = [p for p in (data.get('recent_projects') or []) if p != path]
    data['recent_projects'] = items
    _save_all(data)
    return items


def get_cooldown_days() -> int:
    """更新候補から除外する公開後経過日数 (供給チェーン攻撃対策バッファ)。"""
    val = _load_all().get('cooldown_days', _DEFAULT_COOLDOWN_DAYS)
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return _DEFAULT_COOLDOWN_DAYS


def set_cooldown_days(days: int) -> None:
    data = _load_all()
    try:
        data['cooldown_days'] = max(0, int(days))
    except (TypeError, ValueError):
        data['cooldown_days'] = _DEFAULT_COOLDOWN_DAYS
    _save_all(data)


def get_registry_url() -> str:
    return (_load_all().get('registry_url') or '').strip() or DEFAULT_REGISTRY_URL


def get_proxy_url() -> str:
    """HTTP/HTTPS プロキシ URL。空文字なら未使用 (環境変数の HTTP(S)_PROXY を尊重)。"""
    return (_load_all().get('proxy_url') or '').strip()


def get_osv_api_url() -> str:
    return (_load_all().get('osv_api_url') or '').strip() or DEFAULT_OSV_API_URL


def get_parallel_requests() -> int:
    val = _load_all().get('parallel_requests', DEFAULT_PARALLEL_REQUESTS)
    try:
        return max(1, min(32, int(val)))
    except (TypeError, ValueError):
        return DEFAULT_PARALLEL_REQUESTS


def set_setting(key: str, value) -> None:
    """空文字 / None ならキーを削除 (= デフォルトに戻す)。それ以外は保存。"""
    data = _load_all()
    if value in ('', None):
        data.pop(key, None)
    else:
        data[key] = value
    _save_all(data)
