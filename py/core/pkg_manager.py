"""Python パッケージマネージャ自動検出と install コマンド構築。

uv.lock / poetry.lock / Pipfile(.lock) からプロジェクトの PM を推定し、
それぞれの「最新版に上げる」コマンド文字列を組み立てる。
Global インストールは常に `pip install -U` を使う (Global タブは
`pip list` の結果を見ているので、その site-packages を更新するため)。

Node 側 node/core/pkg_manager.py と同じインターフェース:
  detect(project_path) -> str
  install_command(pm, specs, global_install=, dry_run=) -> str
  run_dry_run(pm, specs, cwd=, global_install=, timeout=) -> (stdout, stderr, rc)
  supports_dry_run(pm, global_install=) -> bool
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def detect(project_path: Path) -> str:
    """Return 'uv' | 'poetry' | 'pipenv' | 'pip'."""
    if (project_path / 'uv.lock').exists():
        return 'uv'
    if (project_path / 'poetry.lock').exists():
        return 'poetry'
    if (project_path / 'Pipfile.lock').exists() or (project_path / 'Pipfile').exists():
        return 'pipenv'
    return 'pip'


def install_command(
    pm: str,
    specs: list[str],
    global_install: bool = False,
    dry_run: bool = False,
) -> str:
    """`<pm> add foo==1.2.3 bar==2.0.0 [--dry-run]` 風のコマンド文字列を組み立てる。

    global は常に pip (`pip install -U`) を使う。Global タブの一覧と整合性を取るため。
    """
    spec_str = ' '.join(specs)

    if global_install:
        cmd = f'pip install -U {spec_str}'
        if dry_run:
            cmd += ' --dry-run'
        return cmd

    if pm == 'uv':
        # `uv add` は pyproject.toml も書き換える (Poetry の add と同様)。
        # 2025-05 時点で `uv add --dry-run` は安定しないので dry-run は別系統。
        return f'uv add {spec_str}'
    if pm == 'poetry':
        cmd = f'poetry add {spec_str}'
        if dry_run:
            cmd += ' --dry-run'
        return cmd
    if pm == 'pipenv':
        # pipenv install は --dry-run を持たない。実行時の差分プレビューは不可。
        return f'pipenv install {spec_str}'
    # default: pip
    cmd = f'pip install -U {spec_str}'
    if dry_run:
        cmd += ' --dry-run'
    return cmd


def supports_dry_run(pm: str, global_install: bool = False) -> bool:
    """この PM で --dry-run プレビューを実行する意味があるか。"""
    if global_install:
        return True  # pip --dry-run
    return pm in ('pip', 'poetry')


def run_dry_run(
    pm: str,
    specs: list[str],
    cwd: str | None = None,
    global_install: bool = False,
    timeout: int = 60,
) -> tuple[str, str, int]:
    """dry-run を同期実行 (stdout, stderr, returncode)。サポート外なら rc=-1。"""
    if not supports_dry_run(pm, global_install=global_install):
        return (
            '',
            f'{pm} は --dry-run プレビューに対応していません (Install で本実行してください)。',
            -1,
        )
    cmd = install_command(pm, specs, global_install=global_install, dry_run=True)
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            cwd=cwd,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return '', f'Timed out after {timeout}s', -1
    except (FileNotFoundError, OSError) as e:
        return '', f'{type(e).__name__}: {e}', -1
