"""semver の最小実装。npmChecker.js のロジックを Python に移植。"""
from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(r'(\d+)\.(\d+)(?:\.(\d+))?')


@dataclass(frozen=True)
class Semver:
    major: int
    minor: int
    patch: int

    def gt(self, other: 'Semver') -> bool:
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)


def parse(version: str | None) -> Semver | None:
    if not version:
        return None
    m = _VERSION_RE.search(version)
    if not m:
        return None
    return Semver(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def normalize(raw: str | None) -> str | None:
    """'^2.0.0' / '>=2.0.0 <3.0.0' / '*' → '2.0.0' or None"""
    if not raw or raw in ('*', 'latest', '', 'any'):
        return None
    m = _VERSION_RE.search(raw)
    return m.group(0) if m else None


def classify(current: str | None, latest: str | None) -> str:
    """'latest' | 'minor' | 'major' | 'unknown'"""
    c, l = parse(current), parse(latest)
    if not c or not l:
        return 'unknown'
    if (c.major, c.minor, c.patch) == (l.major, l.minor, l.patch):
        return 'latest'
    if l.major > c.major:
        return 'major'
    return 'minor'


def pick_latest_minor_and_major(current: str | None, all_versions: list[str], overall_latest: str | None) -> tuple[str | None, str | None]:
    """同 major 内の最新 (latestMinor) と、より上の major があれば overall_latest を latestMajor として返す。"""
    cp = parse(current)
    if not cp:
        return None, None

    stable = [v for v in all_versions if v and '-' not in v]
    parsed = [(v, parse(v)) for v in stable]
    parsed = [(v, p) for v, p in parsed if p]
    parsed.sort(key=lambda t: (t[1].major, t[1].minor, t[1].patch), reverse=True)

    latest_minor = None
    for v, p in parsed:
        if p.major == cp.major and p.gt(cp):
            latest_minor = v
            break

    latest_major = None
    lp = parse(overall_latest)
    if lp and lp.major > cp.major:
        latest_major = overall_latest

    return latest_minor, latest_major


# ── npm-semver specifier matcher ───────────────────────────────────────────
# 「package.json の規約 (`^X.Y.Z` / `~X.Y.Z` / 範囲) を尊重した最高版」を
# 計算する用。py 側 pep440.parseable と同じインターフェースを目指す。
# サポート: ^ / ~ / >= / <= / > / < / = / 部分版 (1.x / 1.2.x) / space-AND。
# 未対応 (= '?' sentinel): `||` (OR) / `1.2.3 - 2.3.4` (hyphen range) /
#                          URL specs (file: / git+ / npm: / workspace: 等) /
#                          tagged versions (latest / next 等の dist-tag 参照)

# 部分版パース: '1.2.3' / '1.2' / '1' / '1.x' / '1.2.x' に対応
_PARTIAL_RE = re.compile(
    r'^v?(\d+|x|\*|X)'
    r'(?:\.(\d+|x|\*|X))?'
    r'(?:\.(\d+|x|\*|X))?'
    r'(?:[-+].*)?$'
)
# URL / git / npm-tag 形式: matcher 対象外として弾く
_NON_VERSION_PREFIXES = (
    'file:', 'link:', 'git+', 'git:', 'git@',
    'http://', 'https://', 'ssh://', 'npm:', 'workspace:',
)


def _parse_partial(s: str) -> tuple[int | None, int | None, int | None] | None:
    """'1.2.3' → (1,2,3) / '1.x' → (1,None,None) / 解釈不能 → None。"""
    m = _PARTIAL_RE.match(s.strip())
    if not m:
        return None
    parts: list[int | None] = []
    for g in m.groups()[:3]:
        if g is None or g in ('x', 'X', '*'):
            parts.append(None)
        else:
            parts.append(int(g))
    return parts[0], parts[1], parts[2]


def _expand_primary(primary: str) -> list[tuple[str, tuple[int, int, int]]] | None:
    """`^1.2.3` / `~1.2` / `>=1` / `1.2.x` / `1.2.3` を AND clauses に展開。

    戻り値: [(op, (M, m, p)), ...]。解釈不能なら None。
    """
    s = primary.strip()
    if not s:
        return None

    # ^ caret
    if s.startswith('^'):
        parsed = _parse_partial(s[1:])
        if not parsed:
            return None
        M, m, p = parsed
        if M is None:
            return None
        if m is None:
            # ^X → >=X.0.0, <(X+1).0.0
            return [('>=', (M, 0, 0)), ('<', (M + 1, 0, 0))]
        if p is None:
            # ^X.Y → >=X.Y.0, <(X+1).0.0 (or with 0-major rule)
            p = 0
        # ^X.Y.Z: range depends on the leading non-zero component
        if M >= 1:
            return [('>=', (M, m, p)), ('<', (M + 1, 0, 0))]
        if m >= 1:
            return [('>=', (M, m, p)), ('<', (0, m + 1, 0))]
        return [('>=', (M, m, p)), ('<', (0, 0, p + 1))]

    # ~ tilde
    if s.startswith('~'):
        parsed = _parse_partial(s[1:])
        if not parsed:
            return None
        M, m, p = parsed
        if M is None:
            return None
        if m is None:
            return [('>=', (M, 0, 0)), ('<', (M + 1, 0, 0))]
        if p is None:
            p = 0
        return [('>=', (M, m, p)), ('<', (M, m + 1, 0))]

    # >= / <= / > / < / = (順序が重要: 長い方を先に)
    for op in ('>=', '<=', '>', '<', '='):
        if s.startswith(op):
            rest = s[len(op):].strip()
            parsed = _parse_partial(rest)
            if not parsed:
                return None
            M, m, p = parsed
            if M is None:
                return None
            if m is None:
                m = 0
            if p is None:
                p = 0
            real_op = '==' if op == '=' else op
            return [(real_op, (M, m, p))]

    # 裸の部分版: 1.2.3 / 1.2.x / 1.x
    parsed = _parse_partial(s)
    if not parsed:
        return None
    M, m, p = parsed
    if M is None:
        return None
    if m is None:
        # X → matches anything starting with X
        return [('>=', (M, 0, 0)), ('<', (M + 1, 0, 0))]
    if p is None:
        # X.Y → matches anything starting with X.Y
        return [('>=', (M, m, 0)), ('<', (M, m + 1, 0))]
    # X.Y.Z → 完全一致
    return [('==', (M, m, p))]


def _split_clauses(spec: str) -> list[tuple[str, tuple[int, int, int]]] | None:
    """spec 全体を AND clauses に分解。'||' / hyphen-range / URL は None。"""
    s = spec.strip()
    if '||' in s:
        return None
    # hyphen range '1.2.3 - 2.3.4' (spaces around -). 単体の '1.2.3-beta' は対象外。
    if ' - ' in s:
        return None
    for prefix in _NON_VERSION_PREFIXES:
        if s.startswith(prefix):
            return None
    primaries = s.split()
    if not primaries:
        return None
    out: list[tuple[str, tuple[int, int, int]]] = []
    for p in primaries:
        sub = _expand_primary(p)
        if sub is None:
            return None
        out.extend(sub)
    return out or None


def _match_clause(v: tuple[int, int, int], op: str, target: tuple[int, int, int]) -> bool:
    if op == '==':
        return v == target
    if op == '>=':
        return v >= target
    if op == '<=':
        return v <= target
    if op == '>':
        return v > target
    if op == '<':
        return v < target
    return False


def matches_spec(version: str | None, spec: str | None) -> bool:
    """version が spec を満たすか。プレリリースは spec 側にも pre 表記が無ければ除外。"""
    if not version or not spec:
        return False
    # プレリリース (foo-beta.1) は基本除外する (npm の慣例と整合)
    if '-' in version:
        return False
    sv = parse(version)
    if not sv:
        return False
    clauses = _split_clauses(spec)
    if not clauses:
        return False
    v = (sv.major, sv.minor, sv.patch)
    return all(_match_clause(v, op, t) for op, t in clauses)


def parseable_spec(spec: str | None) -> bool:
    """spec が我々の matcher で扱える形式かどうか。

    None / 空文字 / wildcard (`*`/`x`/`latest`) は False (= 別扱い)。
    """
    if not spec or not isinstance(spec, str):
        return False
    s = spec.strip()
    if not s or s in ('*', 'x', 'X', 'latest'):
        return False
    return _split_clauses(s) is not None


def is_wildcard_spec(spec: str | None) -> bool:
    """`*` / `x` / `X` / `latest` / 空文字 = 「全バージョン許可」を意味する spec。"""
    if not spec or not isinstance(spec, str):
        return False
    return spec.strip() in ('', '*', 'x', 'X', 'latest')


def latest_matching(all_versions: list[str], spec: str | None) -> str | None:
    """spec を満たす最高安定版。マッチなし / spec 空 / 解釈不能 は None。"""
    if not spec:
        return None
    matching: list[tuple[str, Semver]] = []
    for v in all_versions:
        if not matches_spec(v, spec):
            continue
        sv = parse(v)
        if sv:
            matching.append((v, sv))
    if not matching:
        return None
    matching.sort(key=lambda t: (t[1].major, t[1].minor, t[1].patch), reverse=True)
    return matching[0][0]
