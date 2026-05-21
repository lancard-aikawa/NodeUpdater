"""グローバルインストール済み npm パッケージの列挙。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from shared import debug_log

# 直近の list_global_packages 呼出失敗時の診断情報。
# (exit_code, stderr, stdout 先頭) を保持し、UI 側から status バーに表示できる。
last_error: dict | None = None

# `npm root -g` の結果のプロセス内キャッシュ。npm プロセス起動コスト
# (特に NVM shim + AV スキャン) を毎回払うのを避けるため。
# Node バージョン切替で path が変わる可能性に備え、利用前に dir 存在チェック。
_global_root_cache: str | None = None


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


def _run(args: list[str], timeout: int = 90) -> str | None:
    """args の先頭 ('npm' 等) を絶対パスに解決して subprocess.run。

    解決できなければ last_error にその旨を記録して None を返す。
    失敗時は debug_log にも残す (UI から Debug Log… で確認できる)。

    タイムアウトは 90 秒 (デフォルト)。`npm list -g` は環境によって 30 秒を
    超えることがある (NVM 経由 / AV スキャン / cache miss / SSD 飽和等)。
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
    """`npm root -g` の結果をプロセス内キャッシュして返す。

    1 回 npm を起動して結果を保持。次回以降は path の存在確認だけで即返す。
    Node を NVM で切替えた等でキャッシュした path が消えていたら再取得する。
    """
    global _global_root_cache
    if _global_root_cache and Path(_global_root_cache).is_dir():
        return _global_root_cache
    out = _run(['npm', 'root', '-g'])
    if out:
        _global_root_cache = out.strip()
    return _global_root_cache


def list_global_packages() -> list[dict]:
    """グローバルパッケージ一覧を [{name, version}, ...] で返す。

    Strategy:
      1. `npm root -g` で global node_modules path を取得 (軽量)
      2. その配下を **直接走査** して各 package.json を読む (npm list -g より高速)
      3. 失敗時 (権限/破損/想定外構造) は従来の `npm list -g --json` に fall back

    高速化の理由: `npm list -g` は依存ツリー全体を解析するため NVM/AV 環境では
    30 秒以上かかることがある。直接走査は N 個の package.json を読むだけで
    1 桁速くなる。
    """
    root = global_root()
    if root:
        packages = _list_via_filesystem(root)
        if packages:
            return packages
    # 直接走査が空 / root 不明 → 従来の npm list に fall back
    return _list_via_npm_cli()


def _list_via_filesystem(root: str) -> list[dict]:
    """`<npm root -g>` 配下の通常 / scoped パッケージを直接走査して返す。

    通常: <root>/<pkg>/package.json
    scoped: <root>/@scope/<pkg>/package.json
    `.bin` / `.cache` 等の dot dir は skip。symlink (npm link) も dir 扱いで読む。
    """
    global last_error
    root_path = Path(root)
    if not root_path.is_dir():
        last_error = {'reason': f'npm root -g not a directory: {root}'}
        debug_log.log('npm_global._list_via_filesystem',
                      reason='root path not a directory', root=root)
        return []
    packages: list[dict] = []
    try:
        for entry in root_path.iterdir():
            if not entry.is_dir() or entry.name.startswith('.'):
                continue
            if entry.name.startswith('@'):
                # scoped: 1 階層下を見る
                try:
                    for sub in entry.iterdir():
                        if sub.is_dir():
                            pkg = _read_pkg_name_version(sub / 'package.json')
                            if pkg:
                                packages.append(pkg)
                except OSError:
                    continue
            else:
                pkg = _read_pkg_name_version(entry / 'package.json')
                if pkg:
                    packages.append(pkg)
    except OSError as e:
        last_error = {'reason': f'iterdir failed: {e}'}
        debug_log.log('npm_global._list_via_filesystem',
                      reason='iterdir failed', error=str(e), root=root)
        return []
    last_error = None
    debug_log.log('npm_global._list_via_filesystem',
                  root=root, count=len(packages))
    return packages


def _read_pkg_name_version(path: Path) -> dict | None:
    """package.json から name / version を抽出。読めなければ None。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get('name')
    ver = data.get('version')
    if not isinstance(name, str) or not name:
        return None
    return {'name': name, 'version': ver if isinstance(ver, str) and ver else None}


def _list_via_npm_cli() -> list[dict]:
    """フォールバック: 従来の `npm list -g --depth=0 --json`。

    audit / fund / update-notifier の副作用を flag で無効化して若干高速化。
    それでも 30〜90 秒かかる可能性があるので、通常は直接走査が優先される。
    """
    global last_error
    out = _run([
        'npm', 'list', '-g', '--depth=0', '--json',
        '--no-audit', '--no-fund',
    ])
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
