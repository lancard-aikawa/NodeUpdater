"""プロジェクトのパッケージマネージャ自動検出とコマンド構築。

bun.lock / bun.lockb → bun, pnpm-lock.yaml → pnpm, yarn.lock → yarn、
それ以外は npm。グローバルインストールは常に npm を使う (システム的に
最も汎用なため)。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def detect(project_path: Path) -> str:
    """プロジェクトの PM を 'bun' / 'pnpm' / 'yarn' / 'npm' で返す。"""
    if (project_path / 'bun.lock').exists() or (project_path / 'bun.lockb').exists():
        return 'bun'
    if (project_path / 'pnpm-lock.yaml').exists():
        return 'pnpm'
    if (project_path / 'yarn.lock').exists():
        return 'yarn'
    return 'npm'


def install_command(
    pm: str,
    specs: list[str],
    global_install: bool = False,
    dry_run: bool = False,
) -> str:
    """`<pm> add foo@1.0.0 bar@2.0.0 [--dry-run] [-g]` のコマンド文字列を組み立てる。

    yarn (v1) は dry-run のサポートが弱いので、コマンドはそのまま組み立てて
    実行側で returncode != 0 を見て扱う。
    """
    spec_str = ' '.join(specs)
    if pm == 'bun':
        head = 'bun add'
        if global_install:
            head += ' -g'
        cmd = f'{head} {spec_str}'
        if dry_run:
            cmd += ' --dry-run'
        return cmd
    if pm == 'pnpm':
        head = 'pnpm add'
        if global_install:
            head += ' -g'
        # pnpm add 自体は --dry-run を持たないので install --dry-run でプレビュー
        if dry_run:
            return f'pnpm install {spec_str} --dry-run'
        return f'{head} {spec_str}'
    if pm == 'yarn':
        # yarn v1: yarn add / yarn global add。dry-run は実質非対応 (試行は許可)
        head = 'yarn global add' if global_install else 'yarn add'
        cmd = f'{head} {spec_str}'
        if dry_run:
            cmd += ' --dry-run'  # 多くの場合 ignored or error。
        return cmd
    # default: npm
    head = 'npm install'
    if global_install:
        head += ' -g'
    cmd = f'{head} {spec_str}'
    if dry_run:
        cmd += ' --dry-run'
    return cmd


def run_dry_run(
    pm: str,
    specs: list[str],
    cwd: str | None = None,
    global_install: bool = False,
    timeout: int = 60,
) -> tuple[str, str, int]:
    """dry-run を同期実行 (stdout, stderr, returncode)。タイムアウト/起動失敗時は rc=-1。"""
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
