"""グローバル / カレント Python 環境のインストール済みパッケージ列挙。

pip / uv の `list --format=json` を呼ぶ。グローバルといっても Python は
インタプリタごと (user-site, venv, conda, ...) に独立しているので、ここで
列挙するのは「今 PATH にある python (または uv tool) が見ているサイト」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

from shared import debug_log


def _run(args: list[str], timeout: int = 30) -> str | None:
    """失敗時の診断情報を debug_log に残す (UI から Debug Log… で確認できる)。"""
    cmd_str = ' '.join(args)
    t0 = time.monotonic()
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
    except FileNotFoundError as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        debug_log.log(
            'pip_global._run',
            level='ERROR',
            summary=f'spawn 失敗 ({duration_ms}ms): {cmd_str}',
            reason='FileNotFoundError', duration_ms=duration_ms,
            detail={'cmd': cmd_str, 'error': str(e)},
        )
        return None
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - t0) * 1000)
        debug_log.log(
            'pip_global._run',
            level='ERROR',
            summary=f'timeout {timeout}s: {cmd_str}',
            reason='timeout', timeout_s=timeout, duration_ms=duration_ms,
            detail={'cmd': cmd_str},
        )
        return None
    duration_ms = int((time.monotonic() - t0) * 1000)
    if not result.stdout:
        debug_log.log(
            'pip_global._run',
            level='WARN',
            summary=f'empty stdout rc={result.returncode} ({duration_ms}ms): {cmd_str}',
            reason='empty stdout', rc=result.returncode, duration_ms=duration_ms,
            detail={
                'cmd': cmd_str,
                'stderr': (result.stderr or '').strip(),
            },
        )
    else:
        debug_log.log(
            'pip_global._run',
            level='INFO',
            summary=f'rc={result.returncode} ({duration_ms}ms): {cmd_str}',
            rc=result.returncode, duration_ms=duration_ms, stdout_len=len(result.stdout),
            detail={
                'cmd': cmd_str,
                'stdout_head': result.stdout[:2000],
                'stderr': (result.stderr or '').strip(),
            },
        )
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


def pip_command_str() -> str:
    """list_global_packages() と同じインタプリタを指す pip 起動文字列を返す。

    Global の install を一覧と同一インタプリタに揃えるための関数。

    Windows では `py` (ランチャの既定 python) / `python` (PATH 上の python.exe) /
    `pip` (PATH 上の pip.exe = どこかの python の Scripts) が **別々の python を
    指すことがある**。一覧は `py -m pip list` で取るのに install を bare `pip` で
    打つと、入れた先 (pip の python) と一覧 (py の python) がズレて「更新しても
    反映されない」事故になる。そこで install も _python_argv_prefix() に揃え、
    `<python> -m pip` 形式 (その python 自身の pip モジュール) で実行する。
    """
    prefix = _python_argv_prefix()
    if not prefix:
        return 'pip'  # python が見つからない時は従来どおり bare pip にフォールバック
    exe, *rest = prefix
    # sys.executable 等にスペースが含まれる場合に備えて quote (py / python は不要)
    if ' ' in exe and not exe.startswith('"'):
        exe = f'"{exe}"'
    return ' '.join([exe, *rest])


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
