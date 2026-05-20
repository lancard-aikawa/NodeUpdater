"""グローバルインストール済み npm パッケージの列挙。"""
from __future__ import annotations

import json
import subprocess
import sys


def _run(args: list[str], timeout: int = 30) -> str | None:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            shell=(sys.platform == 'win32'),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    # npm list -g は警告で exit code 1 を返すことがあるが stdout に JSON は出ているので無視
    return result.stdout


def global_root() -> str | None:
    out = _run(['npm', 'root', '-g'])
    return out.strip() if out else None


def list_global_packages() -> list[dict]:
    """`npm list -g --depth=0 --json` の結果を [{name, version}, ...] に整形。"""
    out = _run(['npm', 'list', '-g', '--depth=0', '--json'])
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    deps = data.get('dependencies') or {}
    packages = []
    for name, info in deps.items():
        packages.append({'name': name, 'version': (info or {}).get('version')})
    return packages


def open_command_prompt(cmd: str, cwd: str | None = None) -> None:
    """新しいコンソールウィンドウで任意のコマンドを起動する (GUI はブロックしない)。

    Windows は cmd /K で実行後もウィンドウを残してログを確認可能にする。
    """
    if sys.platform == 'win32':
        subprocess.Popen(
            ['cmd', '/K', cmd],
            cwd=cwd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen(
            ['sh', '-c', f'{cmd}; echo; read -p "Press Enter to close..."'],
            cwd=cwd,
        )


def open_install_prompt(
    name: str,
    version: str | None = None,
    cwd: str | None = None,
    global_install: bool = False,
) -> str:
    """新しいコンソールウィンドウで `npm install [-g] <name>[@version]` を起動する。

    返り値は表示用に組み立てたコマンド文字列。
    """
    target = f'{name}@{version}' if version else f'{name}@latest'
    g = '-g ' if global_install else ''
    cmd = f'npm install {g}{target}'
    open_command_prompt(cmd, cwd=cwd)
    return cmd


def run_npm_audit(cwd: str) -> dict | None:
    """`npm audit --json` を当該プロジェクトで実行し、パース結果を返す。

    npm audit は脆弱性が見つかると exit code != 0 を返すが、stdout には
    有効な JSON が出力されるためそれを採用する。失敗時 (npm 未インストール
    / タイムアウト / JSON 不正) は None。
    """
    try:
        result = subprocess.run(
            ['npm', 'audit', '--json'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=120,
            shell=(sys.platform == 'win32'),
            cwd=cwd,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not result.stdout:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
