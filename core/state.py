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
