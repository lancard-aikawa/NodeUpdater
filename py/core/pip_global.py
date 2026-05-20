"""グローバル / カレント Python 環境のインストール済みパッケージ列挙。

pip / uv の `list --format=json` を呼ぶ。グローバルといっても Python は
インタプリタごと (user-site, venv, conda, ...) に独立しているので、ここで
列挙するのは「今 PATH にある python (または uv tool) が見ているサイト」。
"""
from __future__ import annotations

import json
import shutil
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
    return result.stdout


def _python_argv_prefix() -> list[str] | None:
    # PyInstaller --onefile では sys.executable がこの exe 自身を指す。`-m pip` を
    # 付けて呼ぶと bootloader が引数を無視して GUI を再起動し、無限にウィンドウが
    # 立ち上がる (fork bomb)。frozen 時は PATH 上の本物の Python を探して使う。
    if getattr(sys, 'frozen', False):
        for cand in ('py', 'python', 'python3'):
            if shutil.which(cand):
                return [cand, '-m', 'pip']
        return None
    return [sys.executable, '-m', 'pip']


def list_global_packages() -> list[dict]:
    """`pip list --format=json` の結果を [{name, version}, ...] に整形。

    PEP 8 正規化名 (lowercase) で揃える。失敗時は空リスト。
    """
    prefix = _python_argv_prefix()
    if not prefix:
        return []
    out = _run([*prefix, 'list', '--format=json'])
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    packages = []
    for entry in data:
        name = (entry or {}).get('name')
        ver = (entry or {}).get('version')
        if name:
            packages.append({'name': str(name), 'version': ver})
    return packages


def open_command_prompt(cmd: str, cwd: str | None = None) -> None:
    """新しいコンソールウィンドウで任意のコマンドを起動する (GUI はブロックしない)。

    Node 側 npm_global.open_command_prompt と同じ挙動。
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
