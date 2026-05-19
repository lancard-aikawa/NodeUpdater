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


def open_install_prompt(
    name: str,
    version: str | None = None,
    cwd: str | None = None,
    global_install: bool = False,
) -> str:
    """新しいコンソールウィンドウで `npm install [-g] <name>[@version]` を起動する。

    - global_install=True で `-g` 付き（グローバル更新）
    - cwd を指定すれば、その作業ディレクトリでプロジェクト依存を更新
    - cmd /K で実行後もウィンドウを残し、エラーや警告が読める状態にする
    - GUI 側はブロックしない

    返り値は表示用に組み立てたコマンド文字列。
    """
    target = f'{name}@{version}' if version else f'{name}@latest'
    g = '-g ' if global_install else ''
    cmd = f'npm install {g}{target}'
    if sys.platform == 'win32':
        # CREATE_NEW_CONSOLE で新規ウィンドウを開く。/K で実行後も保持
        subprocess.Popen(
            ['cmd', '/K', cmd],
            cwd=cwd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        # 他 OS は当面サポート外（Windows 専用ツールの想定）
        subprocess.Popen(
            ['sh', '-c', f'{cmd}; echo; read -p "Press Enter to close..."'],
            cwd=cwd,
        )
    return cmd
