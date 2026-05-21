"""pyproject.toml / requirements.txt の読み取り。

優先順:
  1. pyproject.toml [project.dependencies] (PEP 621)
     + [project.optional-dependencies.<group>] / [dependency-groups.<group>]
       (PEP 735, uv 0.4+) を dev 扱い
     + [tool.poetry.dependencies] / [tool.poetry.group.<g>.dependencies] (Poetry)
  2. requirements.txt (pip 形式; -r も再帰)

依存指定は PEP 508 (`name[extra]>=1.0; markers`) を name と version-spec に分解。
version-spec は pep440.normalize() で代表値を抜く。

戻り値は [{name, version, dev, optional_group?}, ...]。
node 側 package_json と同じ形にする。
"""
from __future__ import annotations

import re
import sys
import tomllib  # Python 3.11+
from pathlib import Path

from . import pep440


# PEP 508: name は [A-Za-z0-9][A-Za-z0-9._-]*。extras `[a,b]` は無視。
_REQ_RE = re.compile(
    r'^\s*'
    r'(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)'
    r'\s*'
    r'(?:\[[^\]]*\])?'                # extras (無視)
    r'\s*'
    r'(?P<spec>[<>=!~][^;@]*)?'       # version spec (e.g. ">=1.0,<2.0")
    r'(?:\s*@\s*[^;]+)?'              # direct ref (URL/path) - 無視
    r'(?:\s*;.*)?'                    # environment markers - 無視
    r'\s*$',
)


def _parse_pep508(s: str) -> tuple[str, str | None] | None:
    """'foo[extra]>=1.0; python_version>="3.10"' → ('foo', '>=1.0')。失敗時 None。"""
    if not s or s.lstrip().startswith('#') or s.lstrip().startswith('-'):
        return None
    m = _REQ_RE.match(s)
    if not m:
        return None
    return m.group('name'), (m.group('spec') or '').strip() or None


def _normalize_spec(spec: str | None) -> str | None:
    if not spec:
        return None
    return pep440.normalize(spec)


def read_pyproject(project_path: Path) -> dict | None:
    """pyproject.toml を読んで dict を返す (無ければ None)。"""
    f = project_path / 'pyproject.toml'
    if not f.exists():
        return None
    try:
        return tomllib.loads(f.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _collect_from_list(
    deps: list, dev: bool, group: str | None, out: list[dict],
) -> None:
    for raw in deps or []:
        if not isinstance(raw, str):
            continue
        parsed = _parse_pep508(raw)
        if not parsed:
            continue
        name, spec = parsed
        # python 自身 (Poetry の dependencies に書かれる) は無視
        if name.lower() == 'python':
            continue
        out.append({
            'name': name,
            'version': _normalize_spec(spec),
            'spec': spec or None,  # 生の operator chain (例 '>=2.0,<3') を Wanted 計算用に保持
            'dev': dev,
            'group': group,
        })


def _collect_from_poetry(deps: dict, dev: bool, group: str | None, out: list[dict]) -> None:
    for name, val in (deps or {}).items():
        if name.lower() == 'python':
            continue
        if isinstance(val, str):
            spec = val
        elif isinstance(val, dict):
            # {version = "^1.0", optional = true} など
            spec = val.get('version') or ''
        else:
            spec = ''
        out.append({
            'name': name,
            'version': _normalize_spec(spec),
            'spec': spec or None,
            'dev': dev,
            'group': group,
        })


def collect_from_pyproject(project_path: Path) -> list[dict]:
    """pyproject.toml 1 ファイルから依存を列挙。lock は見ない。"""
    data = read_pyproject(project_path)
    if not data:
        return []

    out: list[dict] = []

    # PEP 621 標準: [project]
    project = data.get('project') or {}
    _collect_from_list(project.get('dependencies') or [], dev=False, group=None, out=out)
    for group_name, deps in (project.get('optional-dependencies') or {}).items():
        is_dev = group_name.lower() in ('dev', 'test', 'tests', 'lint', 'docs', 'typing')
        _collect_from_list(deps, dev=is_dev, group=group_name, out=out)

    # PEP 735: [dependency-groups]
    for group_name, deps in (data.get('dependency-groups') or {}).items():
        # PEP 735 dependency-groups は include-group をサポートするが、ここでは
        # 単純な list[str] のみ扱う (lock がない時の最低限スキャン用途)。
        if isinstance(deps, list):
            is_dev = group_name.lower() in ('dev', 'test', 'tests', 'lint', 'docs', 'typing')
            _collect_from_list(deps, dev=is_dev, group=group_name, out=out)

    # Poetry: [tool.poetry.dependencies] / [tool.poetry.group.<g>.dependencies]
    tool = data.get('tool') or {}
    poetry = tool.get('poetry') or {}
    if poetry:
        _collect_from_poetry(poetry.get('dependencies') or {}, dev=False, group=None, out=out)
        for gname, gobj in (poetry.get('group') or {}).items():
            is_dev = gname.lower() in ('dev', 'test', 'tests', 'lint', 'docs', 'typing')
            _collect_from_poetry(
                (gobj or {}).get('dependencies') or {}, dev=is_dev, group=gname, out=out,
            )

    # 重複除去 (同名は最初に出たエントリを残す。Poetry と PEP 621 を両方書く
    # プロジェクトでもブレないようにする)。
    seen: dict[str, dict] = {}
    deduped: list[dict] = []
    for d in out:
        if d['name'] not in seen:
            seen[d['name']] = d
            deduped.append(d)
    return deduped


_REQ_INCLUDE_RE = re.compile(r'^\s*-r\s+(\S+)')


def collect_from_requirements(
    req_file: Path, dev: bool = False, _seen: set[Path] | None = None,
) -> list[dict]:
    """requirements.txt 1 ファイルを再帰的にパース。`-r other.txt` も追跡。"""
    if _seen is None:
        _seen = set()
    real = req_file.resolve()
    if real in _seen or not real.exists():
        return []
    _seen.add(real)

    out: list[dict] = []
    try:
        for line in real.read_text(encoding='utf-8').splitlines():
            inc = _REQ_INCLUDE_RE.match(line)
            if inc:
                child = (real.parent / inc.group(1)).resolve()
                out.extend(collect_from_requirements(child, dev=dev, _seen=_seen))
                continue
            parsed = _parse_pep508(line)
            if not parsed:
                continue
            name, spec = parsed
            out.append({
                'name': name,
                'version': _normalize_spec(spec),
                'spec': spec or None,
                'dev': dev,
                'group': None,
            })
    except OSError:
        return out
    return out


def collect_dependencies(project_path: Path) -> list[dict]:
    """プロジェクトから直接依存を抽出する (lock は見ない)。

    優先順:
      1. pyproject.toml があればそれを正とする (Poetry / PEP 621)
      2. 無ければ requirements.txt + requirements-dev.txt をマージ
    返り値: [{name, version, dev, group}, ...]
    """
    deps = collect_from_pyproject(project_path)
    if deps:
        return deps

    out: list[dict] = []
    seen: set[str] = set()
    for candidate, is_dev in [
        ('requirements.txt', False),
        ('requirements-dev.txt', True),
        ('requirements_dev.txt', True),
        ('dev-requirements.txt', True),
    ]:
        f = project_path / candidate
        if not f.exists():
            continue
        for d in collect_from_requirements(f, dev=is_dev):
            if d['name'] not in seen:
                seen.add(d['name'])
                out.append(d)
    return out


def project_name(project_path: Path) -> str:
    """pyproject.toml の [project].name または親ディレクトリ名。"""
    data = read_pyproject(project_path)
    if data:
        name = ((data.get('project') or {}).get('name')
                or ((data.get('tool') or {}).get('poetry') or {}).get('name'))
        if isinstance(name, str) and name.strip():
            return name.strip()
    return project_path.name
