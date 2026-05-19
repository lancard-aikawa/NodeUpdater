"""更新履歴 (install 試行ログ) の永続化。

`root_dir()/history/<safe-project>.json` に追記する。1 件は dict:

    {
      "ts": "2026-05-19T20:30:00+09:00",
      "project": "C:/Repos/mywork/foo",
      "workspace": "" or "functions",
      "pm": "bun" | "npm" | ...,
      "scope": "project" | "global",
      "specs": ["foo@1.2.3", ...],
      "from": {"foo": "1.0.0"}   # 把握できれば現行版を併記
    }

実行コマンドは新規プロンプトで動くため、成功/失敗の自動追跡はしない。
ユーザーの操作意図 (どのバージョンに上げようとしたか) を残す目的。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import cache

_HISTORY_DIRNAME = 'history'
_MAX_ENTRIES = 500  # 古い順に切り捨てる上限
_SAFE_KEY = re.compile(r'[^A-Za-z0-9._-]+')


def _history_dir() -> Path:
    d = cache.root_dir() / _HISTORY_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _project_file(project_path: str | Path) -> Path:
    safe = _SAFE_KEY.sub('_', str(project_path))[:120]
    return _history_dir() / f'{safe}.json'


def append(
    project_path: str | Path,
    pm: str,
    scope: str,
    specs: list[str],
    workspace: str = '',
    from_versions: dict[str, str | None] | None = None,
) -> None:
    """履歴に 1 件追記。具体的なフィールドは module docstring 参照。"""
    file = _project_file(project_path)
    entries = read(project_path)
    entries.append({
        'ts': datetime.now().astimezone().isoformat(timespec='seconds'),
        'project': str(project_path),
        'workspace': workspace or '',
        'pm': pm,
        'scope': scope,
        'specs': list(specs),
        'from': dict(from_versions or {}),
    })
    if len(entries) > _MAX_ENTRIES:
        entries = entries[-_MAX_ENTRIES:]
    try:
        file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass  # 書けないなら諦める (次回エントリで再試行)


def read(project_path: str | Path) -> list[dict]:
    """新しい順ではなく書き込み順 (古いものが先頭)。表示側で reverse する。"""
    file = _project_file(project_path)
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def clear(project_path: str | Path) -> None:
    file = _project_file(project_path)
    try:
        file.unlink()
    except OSError:
        pass
