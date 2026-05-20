"""永続ユーザー状態の保存（recent_projects など）。

cache とは別扱い: 期限なし、定期削除されない。
保存先: <exe フォルダ>/state.json または %LOCALAPPDATA%\\NodeUpdater\\state.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from . import cache

# state.json は両 GUI (NodeUpdater / PypkgUpdater) で共有する。
# recent_projects は両方で同じリストを書き込むため、表示時には predicate で
# 「自分の ecosystem の project だけ」に絞る。保存形式は path 文字列のまま
# 変えない (path だけ見れば node/py どちらでも開けるので将来も拡張しやすい)。
ProjectFilter = Callable[[str], bool]

_STATE_FILE = 'state.json'
_MAX_RECENT = 10
_DEFAULT_COOLDOWN_DAYS = 7  # 供給チェーン攻撃対策バッファ (uv/pip のグローバル方針と整合)

DEFAULT_NPM_REGISTRY_URL = 'https://registry.npmjs.org'
DEFAULT_PYPI_INDEX_URL = 'https://pypi.org/pypi'
DEFAULT_OSV_API_URL = 'https://api.osv.dev/v1/querybatch'
DEFAULT_PARALLEL_REQUESTS = 8

# 旧名互換: 既存コードからは get_registry_url() で参照されていたので残しておく。
# 新規コードは get_npm_registry_url() を使う。
DEFAULT_REGISTRY_URL = DEFAULT_NPM_REGISTRY_URL


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


def load_recent_projects(predicate: ProjectFilter | None = None) -> list[str]:
    """recent_projects を返す。predicate を渡すと表示時にさらに絞り込む。

    保存リスト自体は ecosystem 中立 (path のみ)。各 App が自分のマーカー
    (package.json / pyproject.toml など) を predicate に渡して表示を絞る。
    """
    items = _load_all().get('recent_projects') or []
    valid = [p for p in items if isinstance(p, str) and Path(p).is_dir()]
    if predicate:
        return [p for p in valid if predicate(p)]
    return valid


def add_recent_project(path: str, predicate: ProjectFilter | None = None) -> list[str]:
    """先頭に追加して重複除去、存在しないパスを掃除、上限まで切り詰める。

    保存は predicate 非適用 (もう一方の GUI から開かれた project も残す)。
    返り値だけ predicate でフィルタする (combo に流す表示用)。
    """
    path = str(Path(path).resolve())
    data = _load_all()
    items = data.get('recent_projects') or []
    items = [p for p in items if p != path and isinstance(p, str) and Path(p).is_dir()]
    items.insert(0, path)
    items = items[:_MAX_RECENT]
    data['recent_projects'] = items
    _save_all(data)
    if predicate:
        return [p for p in items if predicate(p)]
    return items


def remove_recent_project(path: str, predicate: ProjectFilter | None = None) -> list[str]:
    path = str(Path(path).resolve())
    data = _load_all()
    items = [p for p in (data.get('recent_projects') or []) if p != path]
    data['recent_projects'] = items
    _save_all(data)
    if predicate:
        return [p for p in items if predicate(p)]
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


def get_npm_registry_url() -> str:
    # state.json のキーは 'registry_url' (NodeUpdater 単独時代から続く名)。
    # 新規ツールでも同じ key を共有する (npm 専用設定として残す)。
    return (_load_all().get('registry_url') or '').strip() or DEFAULT_NPM_REGISTRY_URL


def get_pypi_index_url() -> str:
    return (_load_all().get('pypi_index_url') or '').strip() or DEFAULT_PYPI_INDEX_URL


# 旧名互換 (NodeUpdater 単独時代の呼び出し名)。新規コードは get_npm_registry_url を使う。
def get_registry_url() -> str:
    return get_npm_registry_url()


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


def get_github_token() -> str:
    """GitHub API のレート制限緩和用トークン (空文字なら未設定)。"""
    return (_load_all().get('github_token') or '').strip()


def set_setting(key: str, value) -> None:
    """空文字 / None ならキーを削除 (= デフォルトに戻す)。それ以外は保存。"""
    data = _load_all()
    if value in ('', None):
        data.pop(key, None)
    else:
        data[key] = value
    _save_all(data)
