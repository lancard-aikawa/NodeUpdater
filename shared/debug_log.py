"""デバッグ用構造化ログ (JSON Lines)。

サブプロセス呼出 / HTTP 要求 / キャッシュアクセス等の診断トレースを永続化する。
GUI 単体だと「なんで Global packages が出ないんだろう」が原因不明で詰みやすい
ので、最低限の手掛かりを残す。

保存先: cache.root_dir() / 'debug.jsonl'
       (= dev ではリポジトリルート、frozen exe では exe フォルダ or LOCALAPPDATA)

形式: 1 行 = 1 JSON entry。entry の shape は以下:
    {
        "ts":      "ISO-8601 タイムスタンプ",
        "logger":  "短い識別子 (呼び元モジュール名等)",
        "level":   "DEBUG"|"INFO"|"WARN"|"ERROR",
        "summary": "1 行要約 (UI のトップレベル表示)",
        "fields":  {key: value, ...},    # 検索/フィルタ可能なタグ
        "detail":  {key: text or value}, # stdout/stderr/full cmd など (展開時に表示)
    }

`fields` と `detail` の使い分け:
- `fields`: 短い key-value (rc, duration_ms, url, status_code 等)。検索インデックス。
- `detail`: 長文 (stdout/stderr 本文) や JSON dump など。UI の expand 時のみ表示。

サイズが _MAX_BYTES を超えたら次回書き込み時に末尾分だけ残してローテート。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from . import cache

_MAX_BYTES = 2_000_000  # 2 MB を超えたらローテート (構造化で 1 行が長くなる想定)
_KEEP_BYTES = 1_000_000   # ローテート時に末尾何 byte 残すか
_LOG_NAME = 'debug.jsonl'
_LEGACY_LOG_NAME = 'debug.log'  # 旧フラット形式

_VALID_LEVELS = ('DEBUG', 'INFO', 'WARN', 'ERROR')

_lock = threading.Lock()


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
        rotate_marker = json.dumps({
            'ts': datetime.now().astimezone().isoformat(timespec='seconds'),
            'logger': 'debug_log',
            'level': 'INFO',
            'summary': '-- log rotated --',
            'fields': {}, 'detail': {},
        }, ensure_ascii=False) + '\n'
        path.write_bytes(rotate_marker.encode('utf-8') + keep)
    except OSError:
        pass


def log(
    logger: str,
    *,
    summary: str | None = None,
    level: str = 'INFO',
    detail: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    """1 entry 追記。失敗してもサイレントに呑む (デバッグ機能の副作用を絶対起こさない)。

    Args:
        logger: 呼び元の短い識別子。例: 'npm_global._run'
        summary: UI トップレベル表示用の 1 行要約。未指定なら fields から自動生成。
        level: 'DEBUG' / 'INFO' / 'WARN' / 'ERROR'。Tree の色分けに使う。
        detail: 長文や構造化データ (stdout 本文, full cmd 等)。展開時のみ表示。
        **fields: 短い key=value タグ (rc, duration_ms, url, status 等)。

    後方互換: 旧 API `log('tag', reason='...', cmd='...')` 形式の呼び出しも、
    summary を自動生成して受け付ける。
    """
    try:
        # level 正規化
        lv = (level or 'INFO').upper()
        if lv not in _VALID_LEVELS:
            lv = 'INFO'

        # summary 自動生成 (未指定時)。fields から代表的なキーを拾って 1 行に。
        if summary is None:
            summary = _auto_summary(fields)

        entry: dict[str, Any] = {
            'ts':      datetime.now().astimezone().isoformat(timespec='seconds'),
            'logger':  logger,
            'level':   lv,
            'summary': summary,
            'fields':  fields,
            'detail':  detail or {},
        }
        line = json.dumps(entry, ensure_ascii=False, default=str) + '\n'
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


def _auto_summary(fields: dict[str, Any]) -> str:
    """fields から「とりあえず 1 行」を組み立てる (summary 未指定時のフォールバック)。"""
    if not fields:
        return ''
    # よくある key を優先的に拾う
    priority = ['reason', 'url', 'cmd', 'msg']
    for key in priority:
        if key in fields and fields[key]:
            return f'{key}={fields[key]}'
    # 何も無ければ key=value を最大 3 つ連結
    parts = [f'{k}={v}' for k, v in list(fields.items())[:3]]
    return ' '.join(parts)


def read_entries(max_entries: int = 2000) -> list[dict]:
    """末尾 max_entries 件を新しい順 → 古い順で返す (UI 表示用)。

    壊れた行はスキップ。旧 .log フォーマットも検出して legacy エントリとして返す。
    """
    path = log_file_path()
    entries: list[dict] = []
    if path.exists():
        try:
            with path.open('r', encoding='utf-8', errors='replace') as fp:
                # 末尾から N 行抽出: 簡易実装としてファイル全体読み (最大 2 MB なら OK)
                lines = fp.readlines()
            for line in lines[-max_entries:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and 'logger' in obj:
                        entries.append(obj)
                except json.JSONDecodeError:
                    # 不正な行は無視 (ただし将来の見通しのため count を持っても良い)
                    continue
        except OSError:
            pass

    # 旧 .log フォーマットも legacy 表示として拾う
    legacy_path = cache.root_dir() / _LEGACY_LOG_NAME
    if legacy_path.exists():
        try:
            with legacy_path.open('r', encoding='utf-8', errors='replace') as fp:
                for line in fp.readlines()[-200:]:
                    line = line.strip()
                    if line:
                        entries.append({
                            'ts': '',
                            'logger': '(legacy)',
                            'level': 'INFO',
                            'summary': line,
                            'fields': {},
                            'detail': {},
                        })
        except OSError:
            pass

    return entries


def clear() -> None:
    """ログファイルを削除。失敗しても無視。旧 .log も同時に削除。"""
    for name in (_LOG_NAME, _LEGACY_LOG_NAME):
        try:
            (cache.root_dir() / name).unlink(missing_ok=True)
        except OSError:
            pass


# ── 旧 API の薄い後方互換 (テキスト 1 行版を読みたい用) ──────────────────
def read_text(max_bytes: int = 200_000) -> str:
    """末尾 max_bytes 分を文字列で返す。後方互換 (旧 dialog 用)。

    新 dialog は read_entries を使うのでこちらは出番が無くなったが、
    将来「raw を見たい」というニーズが出たとき用に残しておく。
    """
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
        nl = text.find('\n')
        if nl > 0:
            text = text[nl + 1:]
        return '-- (truncated head) --\n' + text
    except OSError:
        return ''
