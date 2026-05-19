"""bun.lock (Bun 1.2+ のテキストロックファイル) のパース。

bun.lock は JSONC (trailing comma あり) で、概略は以下:

    {
      "lockfileVersion": 1,
      "workspaces": {
        "": { "name": "...", "dependencies": {...}, "devDependencies": {...} },
        "<sub-path>": { "name": "...", "dependencies": {...} }
      },
      "packages": {
        "<name-or-parent/name>": [
          "name@version",       // 解決済みの name@version (権威データ)
          "<registry-url>",     // 取得元 (workspaces 等では空または別 URL)
          { "dependencies": {...}, "peerDependencies": {...}, ... },
          "sha512-..."          // 整合性 (workspace/file 形式では省略)
        ]
      }
    }

package_lock.read() と同じ shape の list を返すことで、UI 側 (OSV スキャン
など) は呼び分けるだけで動くようにする。
"""
from __future__ import annotations

import json
from pathlib import Path


def _strip_trailing_commas(text: str) -> str:
    """JSONC → JSON: トレーリングカンマだけ取り除く (文字列内は保護)。

    Bun の lock は標準 JSON にトレーリングカンマだけ加わった方言。`//` コメント
    が将来出てきたら別途対応。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            # 文字列中のエスケープは次の 1 文字とまとめて消費 (\" や \\ の保護)
            if ch == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ',':
            # 次の非空白が } か ] ならカンマを破棄
            j = i + 1
            while j < n and text[j] in ' \t\r\n':
                j += 1
            if j < n and text[j] in '}]':
                i += 1
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


# Bun のバージョンプレフィックスのうち OSV クエリ対象外にすべきもの
_LOCAL_PREFIXES = ('workspace:', 'file:', 'link:', 'git+', 'git:', 'http:', 'https:')


def _parse_name_version(spec: str) -> tuple[str | None, str | None]:
    """'name@version' / '@scope/name@version' をペアに分解。

    workspace:/file:/git+ などローカル/カスタム参照は (None, None) を返す。
    """
    if not isinstance(spec, str) or not spec:
        return None, None
    # スコープ付きは先頭の @ を名前の一部として扱う
    start = 1 if spec.startswith('@') else 0
    at = spec.find('@', start)
    if at < 0:
        return spec, None
    name = spec[:at]
    version = spec[at + 1:]
    if any(version.startswith(p) for p in _LOCAL_PREFIXES):
        return None, None
    return name, version


def read(project_path: Path) -> list[dict]:
    """bun.lock から [{name, version, direct, dev, roots}, ...] を返す。

    workspaces[""] の直接依存をルート扱いにする (サブワークスペースの直接依存は
    推移扱いになるが、reverse グラフ経由でルーツに親ワークスペース名が現れる)。
    """
    lock_file = project_path / 'bun.lock'
    if not lock_file.exists():
        return []
    try:
        raw = lock_file.read_text(encoding='utf-8')
        data = json.loads(_strip_trailing_commas(raw))
    except (OSError, json.JSONDecodeError):
        return []

    workspaces = data.get('workspaces') or {}
    root_ws = workspaces.get('') or {}
    direct: set[str] = set()
    for field in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'):
        direct.update((root_ws.get(field) or {}).keys())
    dev_direct: set[str] = set((root_ws.get('devDependencies') or {}).keys())

    packages = data.get('packages') or {}

    # reverse[child] = {child を要求している親パッケージ名 (もしくは仮想ルート '') の集合}
    reverse: dict[str, set[str]] = {}
    for name in direct:
        reverse.setdefault(name, set()).add('')

    # 各ワークスペース直接依存もグラフに足す (親はワークスペース名)
    for ws_path, ws in workspaces.items():
        if ws_path == '' or not isinstance(ws, dict):
            continue
        ws_name = ws.get('name') or ws_path
        for field in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'):
            for child in (ws.get(field) or {}).keys():
                reverse.setdefault(child, set()).add(ws_name)

    # packages 各エントリを iterate
    seen: dict[str, dict] = {}
    for _key, arr in packages.items():
        if not isinstance(arr, list) or not arr:
            continue
        name, version = _parse_name_version(arr[0])
        if not name or not version:
            continue
        # 依存関係を取り出して reverse グラフに登録
        info = arr[2] if len(arr) >= 3 and isinstance(arr[2], dict) else {}
        for field in ('dependencies', 'peerDependencies', 'optionalDependencies'):
            for child in (info.get(field) or {}).keys():
                reverse.setdefault(child, set()).add(name)
        # 同一 name@version の重複はスキップ
        dedup = f'{name}@{version}'
        if dedup in seen:
            continue
        seen[dedup] = {
            'name': name,
            'version': version,
            'direct': name in direct,
            # 直接依存が dev のみのときは dev=True、そうでなければ False
            # (推移依存の dev 判定は bun の lock には情報が無いので近似)
            'dev': name in dev_direct and name not in (direct - dev_direct),
        }

    def trace_roots(start: str) -> list[str]:
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

    out = []
    for dep in seen.values():
        dep = dict(dep)  # copy
        dep['roots'] = trace_roots(dep['name'])
        out.append(dep)
    return out
