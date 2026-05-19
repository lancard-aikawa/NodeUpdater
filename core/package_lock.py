"""package-lock.json (npm lockfile v2 / v3) のパース。

ルート package.json では直接依存しか分からないため、推移依存も含めて
脆弱性スキャンしたい場合はこのモジュールを使う。
"""
from __future__ import annotations

import json
from pathlib import Path


def read(project_path: Path) -> list[dict]:
    """package-lock.json から全依存を列挙。

    返り値: [{name, version, direct, dev, roots}, ...]
      - direct: ルートが直接 dependencies / devDependencies 等で要求しているか
      - dev: 開発専用ツリーに属するか (npm が記録した `dev` フラグを尊重)
      - roots: そのパッケージを (推移的にでも) 引き込んでいる直接依存名のリスト

    lockfileVersion 2 / 3 の `packages` フィールドのみサポート (npm v7+)。
    v1 形式や lock が無い場合は空リストを返す。

    親追跡は名前単位の reverse グラフで簡略化。同名パッケージが複数版あっても
    親パスは同じ集合になる近似だが、推移依存の出所を把握するには十分。
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

    # reverse[child_name] = {child を直接 require する親パッケージ名の集合}
    # ルート (空文字キー) からの依存は仮想的に親 '' とする。
    reverse: dict[str, set[str]] = {}
    for name in direct:
        reverse.setdefault(name, set()).add('')
    for key, info in packages.items():
        if key == '' or not isinstance(info, dict):
            continue
        idx = key.rfind('node_modules/')
        if idx < 0:
            continue
        parent_name = key[idx + len('node_modules/'):]
        for field in ('dependencies', 'optionalDependencies', 'peerDependencies'):
            for child_name in (info.get(field) or {}).keys():
                reverse.setdefault(child_name, set()).add(parent_name)

    def trace_roots(start: str) -> list[str]:
        """start を引き込むルート側の直接依存名を列挙 (start 自身も直接依存ならそれを含む)。"""
        roots: set[str] = set()
        visited: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in visited or cur == '':
                continue
            visited.add(cur)
            if cur in direct:
                roots.add(cur)
            for p in reverse.get(cur, ()):
                if p not in visited:
                    stack.append(p)
        return sorted(roots)

    seen: dict[str, dict] = {}
    for key, info in packages.items():
        if key == '' or not isinstance(info, dict):
            continue
        idx = key.rfind('node_modules/')
        if idx < 0:
            continue
        name = key[idx + len('node_modules/'):]
        version = info.get('version')
        if not version:
            continue
        dedup = f'{name}@{version}'
        if dedup in seen:
            continue
        seen[dedup] = {
            'name': name,
            'version': version,
            'direct': name in direct,
            'dev': bool(info.get('dev', False)),
            'roots': trace_roots(name),
        }
    return list(seen.values())
