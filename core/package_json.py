"""package.json の読み書き。"""
from __future__ import annotations

import json
from pathlib import Path

from . import semver


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
    """[{name, version, dev}, ...] の形で全依存を列挙。version は正規化済み。"""
    parsed = read(project_path)
    out = []
    for name, raw in parsed['dependencies'].items():
        out.append({'name': name, 'version': semver.normalize(raw), 'dev': False})
    for name, raw in parsed['devDependencies'].items():
        out.append({'name': name, 'version': semver.normalize(raw), 'dev': True})
    return out


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
