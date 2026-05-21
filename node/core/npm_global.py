"""グローバルインストール済み npm パッケージの列挙。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from shared import debug_log

# 直近の list_global_packages 呼出失敗時の診断情報。
# (exit_code, stderr, stdout 先頭) を保持し、UI 側から status バーに表示できる。
last_error: dict | None = None


def _resolve_npm_command() -> list[str] | None:
    """PATH から npm の実体を解決。npm.cmd / npm.exe を明示的に拾う。

    PyInstaller --noconsole や Explorer 起動の GUI など、shell=True の cmd.exe
    探索だけだと PATH 文脈の違いで `'npm' is not recognized` になることがある。
    shutil.which で .cmd / .exe を先に解決しておく。
    """
    # Windows: PATHEXT に従って .cmd / .exe / 拡張子なしの順で探す
    cand = shutil.which('npm')
    if cand:
        return [cand]
    # Windows で PATH に node の bin はあるが PATHEXT が変な場合のバックアップ
    if sys.platform == 'win32':
        for ext in ('.cmd', '.exe', '.bat'):
            cand = shutil.which(f'npm{ext}')
            if cand:
                return [cand]
    return None


def _run(args: list[str], timeout: int = 30) -> str | None:
    """args の先頭 ('npm' 等) を絶対パスに解決して subprocess.run。

    解決できなければ last_error にその旨を記録して None を返す。
    失敗時は debug_log にも残す (UI から Debug Log… で確認できる)。
    """
    global last_error
    resolved_head = _resolve_npm_command()
    if not resolved_head:
        last_error = {
            'reason': 'npm not found in PATH',
            'cmd': ' '.join(args),
        }
        debug_log.log(
            'npm_global._run',
            reason='npm not found in PATH',
            cmd=' '.join(args),
            path_env=(os.environ.get('PATH') or '')[:1000],
            which_npm=shutil.which('npm'),
        )
        return None
    full = resolved_head + args[1:]
    try:
        result = subprocess.run(
            full,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            # 絶対パスを解決済みなので shell=False で OK (cmd 経由の引数解釈差を避ける)
            shell=False,
        )
    except FileNotFoundError as e:
        last_error = {'reason': f'FileNotFoundError: {e}', 'cmd': ' '.join(full)}
        debug_log.log('npm_global._run', reason='FileNotFoundError',
                      error=str(e), cmd=' '.join(full))
        return None
    except subprocess.TimeoutExpired:
        last_error = {'reason': f'Timed out after {timeout}s', 'cmd': ' '.join(full)}
        debug_log.log('npm_global._run', reason='timeout',
                      timeout_s=timeout, cmd=' '.join(full))
        return None
    # 失敗時の診断情報を残す。exit_code != 0 でも stdout に JSON があれば成功扱い。
    if not result.stdout:
        last_error = {
            'reason': f'empty stdout (rc={result.returncode})',
            'cmd': ' '.join(full),
            'stderr': (result.stderr or '').strip()[:400],
        }
        debug_log.log('npm_global._run', reason='empty stdout',
                      cmd=' '.join(full), rc=result.returncode,
                      stderr_head=(result.stderr or '').strip()[:400])
    else:
        last_error = None
        debug_log.log('npm_global._run', cmd=' '.join(full),
                      rc=result.returncode, stdout_len=len(result.stdout))
    return result.stdout


def global_root() -> str | None:
    out = _run(['npm', 'root', '-g'])
    return out.strip() if out else None


def list_global_packages() -> list[dict]:
    """`npm list -g --depth=0 --json` の結果を [{name, version}, ...] に整形。"""
    global last_error
    out = _run(['npm', 'list', '-g', '--depth=0', '--json'])
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        last_error = {'reason': f'JSON parse error: {e}', 'stdout_head': out[:200]}
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
