"""requirements*.txt の version 部分を operator を保ったまま書き換える。

Install 実行で `pip install -U foo==X.Y.Z` を走らせる際、requirements.txt が
そのまま放置されていると次回 `pip install -r requirements.txt` で旧版に
戻ってしまう (典型的なフットガン)。これを避けるため、Install 確定後に
requirements*.txt 側も同期する。

operator スタイルは尊重する:
  foo==1.2.3        → foo==NEW       (exact pin)
  foo~=1.4          → foo~=NEW       (compatible release; precision は新版に従う)
  foo>=2.0          → foo>=NEW       (minimum, 単独)
  foo               → 変更しない      (version 未指定の意図を尊重)
  foo>=1,<2 など複合 → 変更しない      (意図を壊さない側に倒す。skipped 報告)

uv / poetry プロジェクトでは uv add / poetry add 側が pyproject.toml と lock を
書き換えるため、このモジュールは pip プロジェクトでのみ呼ばれる。
"""
from __future__ import annotations

import re
from pathlib import Path

# 行頭から「name [extras] <whitespace> <rest>」を分離。
# rest は version spec + markers + comment 全部を含む。
_LINE_RE = re.compile(
    r'^'
    r'(?P<indent>[ \t]*)'
    r'(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)'
    r'(?P<extras>\[[^\]]*\])?'
    r'(?P<gap>[ \t]*)'
    r'(?P<rest>.*?)'
    r'[ \t]*$'
)

# rest が「単純な単一 operator + version + (markers / comment)」かどうかを判定。
# 複合 (`>=1,<2`) は after に , を含まないのでマッチしない → skipped。
_SIMPLE_REST_RE = re.compile(
    r'^'
    r'(?P<op>==|===|~=|!=|<=|>=|<|>)\s*'
    r'(?P<ver>[0-9][A-Za-z0-9.\-+!]*)'
    r'(?P<after>\s*(?:;[^#]*)?(?:#.*)?)$'
)


def normalize_name(name: str) -> str:
    """PEP 503: lowercase + [_.-] を一本化。比較用キー。"""
    return re.sub(r'[._-]+', '-', name.lower())


# version 書き換えのターゲット operator (これ以外は触らない)
_REWRITABLE_OPS = frozenset({'==', '~=', '>='})


def _rewrite_line(line: str, updates: dict[str, str]) -> tuple[str, str | None, str | None]:
    """1 行を書き換える。

    戻り値: (new_line, matched_name, status)
      matched_name: 更新対象とマッチした正規化名 (マッチしなければ None)
      status: 'updated' | 'skipped: <reason>' | None (= 当該行は更新対象外)
    """
    m = _LINE_RE.match(line)
    if not m:
        return line, None, None
    name = m.group('name')
    norm = normalize_name(name)
    if norm not in updates:
        return line, None, None

    rest = (m.group('rest') or '').strip()
    if not rest:
        # bare name: version 未指定の意図を尊重して触らない
        return line, norm, 'skipped: bare name (no version specifier)'

    rm = _SIMPLE_REST_RE.match(rest)
    if not rm:
        return line, norm, f'skipped: complex spec "{rest}"'

    op = rm.group('op')
    if op not in _REWRITABLE_OPS:
        return line, norm, f'skipped: operator "{op}" not in {sorted(_REWRITABLE_OPS)}'

    new_ver = updates[norm]
    after = rm.group('after') or ''
    new_rest = f'{op}{new_ver}{after}'
    new_line = (
        m.group('indent')
        + name
        + (m.group('extras') or '')
        + m.group('gap')
        + new_rest
    )
    return new_line, norm, f'updated: {op}{new_ver}'


def rewrite_in_file(file_path: Path, updates: dict[str, str]) -> dict[str, str]:
    """ファイル内で updates のキー (正規化名) と一致する行の version を書き換える。

    戻り値: {正規化名 → status_message}。マッチしなかった name は含まれない。
    実際の更新があった場合のみファイルを書き戻す (no-op の場合 mtime を変えない)。
    """
    if not file_path.exists():
        return {}
    try:
        original = file_path.read_text(encoding='utf-8')
    except OSError:
        return {n: 'file read error' for n in updates}

    new_lines: list[str] = []
    statuses: dict[str, str] = {}
    has_change = False
    for line in original.splitlines(keepends=False):
        new_line, norm, status = _rewrite_line(line, updates)
        new_lines.append(new_line)
        if norm and status:
            # 同名が複数行に出るケースは最後の判定を残す
            statuses[norm] = status
            if status.startswith('updated:') and new_line != line:
                has_change = True

    if has_change:
        # 元ファイルの末尾改行有無を保持する
        suffix = '\n' if original.endswith('\n') else ''
        try:
            file_path.write_text('\n'.join(new_lines) + suffix, encoding='utf-8')
        except OSError:
            return {n: 'file write error' for n in updates}
    return statuses


# Project 内で順に当たる候補ファイル。先頭から走査し、見つかった順に updates を消化する。
_CANDIDATE_FILES = [
    'requirements.txt',
    'requirements-dev.txt',
    'requirements_dev.txt',
    'dev-requirements.txt',
    'requirements-test.txt',
    'requirements_test.txt',
]


def rewrite_in_project(
    project_path: Path,
    updates: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Project ルートの requirements*.txt をスキャンして version を更新。

    引数 updates のキーはユーザー入力の name (大小書きまちまち可)。内部で
    正規化してマッチさせる。戻り値: {filename → {normalized_name → status}}。
    """
    norm_updates = {normalize_name(n): v for n, v in updates.items()}
    out: dict[str, dict[str, str]] = {}
    pending = set(norm_updates.keys())
    for fname in _CANDIDATE_FILES:
        if not pending:
            break
        f = project_path / fname
        if not f.exists():
            continue
        statuses = rewrite_in_file(f, norm_updates)
        if statuses:
            out[fname] = statuses
            for name, s in statuses.items():
                if s.startswith('updated:'):
                    pending.discard(name)
    return out


def summarize(per_file: dict[str, dict[str, str]]) -> str:
    """ステータスバー / ログ向けの 1 行サマリ。"""
    updated = sum(
        1 for f in per_file.values() for s in f.values() if s.startswith('updated:')
    )
    skipped = sum(
        1 for f in per_file.values() for s in f.values() if s.startswith('skipped:')
    )
    files = len([f for f in per_file.values() if any(s.startswith('updated:') for s in f.values())])
    parts = []
    if updated:
        parts.append(f'updated {updated} line(s) in {files} file(s)')
    if skipped:
        parts.append(f'skipped {skipped} (complex/bare specs)')
    return '; '.join(parts) if parts else 'no requirements*.txt change'
