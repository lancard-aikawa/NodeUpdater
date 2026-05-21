"""package.json の読み書き。"""
from __future__ import annotations

import json
from pathlib import Path

from . import bun_lock, semver


def read(project_path: Path) -> dict:
    """package.json を読み、{name, dependencies, devDependencies} を返す。"""
    pkg_file = project_path / 'package.json'
    if not pkg_file.exists():
        return {'name': '', 'dependencies': {}, 'devDependencies': {}}
    try:
        pkg = json.loads(pkg_file.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'name': '', 'dependencies': {}, 'devDependencies': {}}
    return {
        'name': pkg.get('name', ''),
        'dependencies': pkg.get('dependencies', {}) or {},
        'devDependencies': pkg.get('devDependencies', {}) or {},
    }


def collect_dependencies(project_path: Path) -> list[dict]:
    """[{name, version, spec, dev}, ...] の形で全依存を列挙。

    version は正規化 (数値のみ; UI の Current 表示用)、spec は package.json の
    raw 文字列 (`^1.2.3` / `~1.0` / `>=1 <2` / `file:./pkg` 等そのまま)。
    spec があれば Wanted 列の計算に使われる。
    """
    parsed = read(project_path)
    out = []
    for name, raw in parsed['dependencies'].items():
        out.append({
            'name': name,
            'version': semver.normalize(raw),
            'spec': raw if isinstance(raw, str) and raw else None,
            'dev': False,
        })
    for name, raw in parsed['devDependencies'].items():
        out.append({
            'name': name,
            'version': semver.normalize(raw),
            'spec': raw if isinstance(raw, str) and raw else None,
            'dev': True,
        })
    return out


def read_installed_version(project_path: Path, name: str) -> str | None:
    """node_modules/<name>/package.json から実際にインストール済みの version を読む。

    未インストール / node_modules 無し / 壊れた JSON は None。
    workspaces は npm/yarn/pnpm のいずれも基本ルート node_modules にホイストする
    ため、project_path はモノレポルートを渡す前提。
    """
    pkg_file = project_path / 'node_modules' / name / 'package.json'
    if not pkg_file.exists():
        return None
    try:
        data = json.loads(pkg_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    v = data.get('version') if isinstance(data, dict) else None
    return v if isinstance(v, str) and v else None


def attach_installed_info(project_path: Path, deps: list[dict]) -> list[dict]:
    """各 dep に `installed_version` (str|None) と `installed` (bool) を付与する。

    deps を in-place で書き換えつつ同オブジェクトを返す (利便性のため)。
    package.json の spec しか持たない deps を、実 node_modules の状態と照合する。
    """
    for d in deps:
        v = read_installed_version(project_path, d.get('name', ''))
        d['installed_version'] = v
        d['installed'] = v is not None
    return deps


def write_dependency(project_path: Path, name: str, version: str, dev: bool) -> None:
    """package.json の dependencies / devDependencies にエントリを追加または更新する。"""
    pkg_file = project_path / 'package.json'
    pkg: dict = {}
    if pkg_file.exists():
        try:
            pkg = json.loads(pkg_file.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            pkg = {}
    key = 'devDependencies' if dev else 'dependencies'
    section = pkg.get(key) or {}
    section[name] = f'^{version}'
    pkg[key] = dict(sorted(section.items()))
    pkg_file.parent.mkdir(parents=True, exist_ok=True)
    pkg_file.write_text(json.dumps(pkg, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def _read_raw(project_path: Path) -> dict | None:
    pkg_file = project_path / 'package.json'
    if not pkg_file.exists():
        return None
    try:
        return json.loads(pkg_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _root_workspace(project_path: Path, pkg: dict | None) -> dict:
    name = ((pkg or {}).get('name') or '') or project_path.name or '.'
    return {'path': '', 'name': name, 'label': f'. (root: {name})'}


def list_workspaces(project_path: Path) -> list[dict]:
    """モノレポなら全ワークスペースを、そうでなければルートだけの list を返す。

    優先順:
      1. bun.lock の workspaces セクション (Bun 1.2+)
      2. package.json の workspaces フィールド (npm/yarn)
      3. 単一プロジェクト
    返り値の各要素: {path, name, label}。先頭は必ずルート ('')。
    """
    pkg = _read_raw(project_path)

    # bun.lock 優先
    bun_data = bun_lock.parse_lock(project_path)
    if bun_data:
        ws = bun_data.get('workspaces') or {}
        if len(ws) > 1:
            entries = []
            for path, info in ws.items():
                name = (info or {}).get('name') or (path or '.')
                if path == '':
                    label = f'. (root: {name})'
                else:
                    label = f'{path}/  ({name})' if name != path else f'{path}/'
                entries.append({'path': path, 'name': name, 'label': label})
            entries.sort(key=lambda w: (w['path'] != '', w['path']))
            return entries

    # npm workspaces
    if pkg:
        ws_field = pkg.get('workspaces')
        ws_paths = ws_field if isinstance(ws_field, list) else (
            (ws_field or {}).get('packages') if isinstance(ws_field, dict) else None
        )
        if ws_paths:
            entries = [_root_workspace(project_path, pkg)]
            seen = {''}
            for pattern in ws_paths:
                try:
                    matches = list(project_path.glob(pattern))
                except (OSError, ValueError):
                    matches = []
                for match in matches:
                    if not match.is_dir() or not (match / 'package.json').exists():
                        continue
                    try:
                        rel = match.relative_to(project_path).as_posix()
                    except ValueError:
                        continue
                    if rel in seen:
                        continue
                    seen.add(rel)
                    sub_pkg = _read_raw(match) or {}
                    sub_name = sub_pkg.get('name') or rel
                    entries.append({
                        'path': rel, 'name': sub_name,
                        'label': f'{rel}/  ({sub_name})' if sub_name != rel else f'{rel}/',
                    })
            return entries

    return [_root_workspace(project_path, pkg)]


def collect_dependencies_at(project_path: Path, workspace_path: str = '') -> list[dict]:
    """指定ワークスペースの直接依存を [{name, version, dev}, ...] で返す。

    workspace_path == '' はルートで従来の collect_dependencies と同じ挙動。
    Bun monorepo では bun.lock の workspaces 情報を優先 (version-spec が
    そのまま入っているため)、無ければサブフォルダの package.json を読む。
    """
    if workspace_path == '':
        return collect_dependencies(project_path)

    # bun.lock 経由
    bun_data = bun_lock.parse_lock(project_path)
    if bun_data:
        ws = (bun_data.get('workspaces') or {}).get(workspace_path)
        if isinstance(ws, dict):
            out = []
            for name, raw in (ws.get('dependencies') or {}).items():
                out.append({
                    'name': name,
                    'version': semver.normalize(raw),
                    'spec': raw if isinstance(raw, str) and raw else None,
                    'dev': False,
                })
            for name, raw in (ws.get('devDependencies') or {}).items():
                out.append({
                    'name': name,
                    'version': semver.normalize(raw),
                    'spec': raw if isinstance(raw, str) and raw else None,
                    'dev': True,
                })
            return out

    # サブフォルダの package.json
    sub_dir = project_path / workspace_path
    if (sub_dir / 'package.json').exists():
        return collect_dependencies(sub_dir)
    return []


def list_subprojects(project_path: Path) -> list[dict]:
    """ルート + 1 階層下の package.json を持つフォルダを列挙。"""
    out = []
    if (project_path / 'package.json').exists():
        out.append({'label': './', 'dir': str(project_path)})
    try:
        for child in sorted(project_path.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith('.') or child.name == 'node_modules':
                continue
            if (child / 'package.json').exists():
                out.append({'label': f'{child.name}/', 'dir': str(child)})
    except OSError:
        pass
    return out
