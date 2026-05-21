"""デバッグ用 append-only ログ。

サブプロセス呼出 (npm/pip 等) や HTTP 失敗の診断を残すために使う。
GUI 単体だと「なんで Global packages が出ないんだろう」が原因不明で
詰みやすいので、最低限の手掛かりを永続ファイルに残す。

保存先: cache.root_dir() / 'debug.log'
       (= dev ではリポジトリルート、frozen exe では exe フォルダ or LOCALAPPDATA)

サイズが _MAX_BYTES を超えたら次回書き込み時に末尾分だけ残してローテート。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from . import cache

_MAX_BYTES = 1_000_000  # 1 MB を超えたらローテート
_KEEP_BYTES = 500_000   # ローテート時に末尾何 byte 残すか
_LOG_NAME = 'debug.log'

_lock = threading.Lock()  # 複数スレッドから log() が呼ばれても安全に


def log_file_path() -> Path:
    return cache.root_dir() / _LOG_NAME


def _rotate(path: Path) -> None:
    """末尾 _KEEP_BYTES だけ残して書き戻す。改行で揃える。"""
    try:
        data = path.read_bytes()
    except OSError:
        return
    keep = data[-_KEEP_BYTES:]
    # 中途半端な行を捨てて clean に
    nl = keep.find(b'\n')
    if nl > 0:
        keep = keep[nl + 1:]
    try:
        path.write_bytes(b'-- log rotated --\n' + keep)
    except OSError:
        pass


def log(tag: str, payload: dict | None = None, **kw) -> None:
    """1 行追記。tag は呼び元モジュール名等の短い識別子。

    payload は dict (JSON 化される)。kw を渡すと payload にマージされる。
    失敗してもサイレントに呑む (デバッグ機能なので副作用を絶対起こさない)。
    """
    try:
        data = dict(payload or {})
        data.update(kw)
        ts = datetime.now().astimezone().isoformat(timespec='seconds')
        line = f'{ts} [{tag}] {json.dumps(data, ensure_ascii=False, default=str)}\n'
        path = log_file_path()
        with _lock:
            try:
                if path.exists() and path.stat().st_size > _MAX_BYTES:
                    _rotate(path)
            except OSError:
                pass
            try:
                with path.open('a', encoding='utf-8') as fp:
                    fp.write(line)
            except OSError:
                pass
    except Exception:
        pass  # デバッグ機能で例外を出すと本機能を壊すので絶対呑む


def read_text(max_bytes: int = 200_000) -> str:
    """末尾 max_bytes だけ読み出して文字列で返す (UI 表示用)。"""
    try:
        path = log_file_path()
        if not path.exists():
            return ''
        size = path.stat().st_size
        if size <= max_bytes:
            return path.read_text(encoding='utf-8', errors='replace')
        with path.open('rb') as fp:
            fp.seek(size - max_bytes)
            data = fp.read()
        text = data.decode('utf-8', errors='replace')
        # 先頭の中途半端な行を捨てる
        nl = text.find('\n')
        if nl > 0:
            text = text[nl + 1:]
        return '-- (truncated head) --\n' + text
    except OSError:
        return ''


def clear() -> None:
    """ログファイルを削除。失敗しても無視。"""
    try:
        log_file_path().unlink(missing_ok=True)
    except OSError:
        pass
