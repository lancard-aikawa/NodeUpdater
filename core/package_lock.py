"""package-lock.json (npm lockfile v2 / v3) のパース。

ルート package.json では直接依存しか分からないため、推移依存も含めて
脆弱性スキャンしたい場合はこのモジュールを使う。
"""
from __future__ import annotations

import json
from pathlib import Path


def read(project_path: Path) -> list[dict]:
    """package-lock.json から全依存を列挙。

    返り値: [{name, version, direct, dev}, ...]
      - direct: ルートが直接 dependencies / devDependencies 等で要求しているか
      - dev: 開発専用ツリーに属するか (npm が記録した `dev` フラグを尊重)

    lockfileVersion 2 / 3 の `packages` フィールドのみサポート (npm v7+)。
    v1 形式や lock が無い場合は空リストを返す。
    """
    lock_file = project_path / 'package-lock.json'
    if not lock_file.exists():
        return []
    try:
        lock = json.loads(lock_file.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return []

    packages = lock.get('packages')
    if not packages:
        return []

    root = packages.get('') or {}
    direct = (
        set((root.get('dependencies') or {}).keys())
        | set((root.get('devDependencies') or {}).keys())
        | set((root.get('optionalDependencies') or {}).keys())
        | set((root.get('peerDependencies') or {}).keys())
    )

    seen: dict[str, dict] = {}
    for key, info in packages.items():
        if key == '' or not isinstance(info, dict):
            continue
        # キー例: "node_modules/foo" / "node_modules/@scope/bar" /
        #         "node_modules/foo/node_modules/nested"
        idx = key.rfind('node_modules/')
        if idx < 0:
            continue
        name = key[idx + len('node_modules/'):]
        version = info.get('version')
        if not version:
            continue
        # name@version で重複排除 (同一版が node_modules ツリーに複数現れるため)
        dedup = f'{name}@{version}'
        if dedup in seen:
            continue
        seen[dedup] = {
            'name': name,
            'version': version,
            'direct': name in direct,
            'dev': bool(info.get('dev', False)),
        }
    return list(seen.values())
