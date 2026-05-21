"""PEP 440 バージョンの最小実装。

semver と違って release segment が任意長 (1.2.3.4 もある) で、
pre/post/dev/local 修飾子が付く。比較は release tuple を主軸にし、
pre は release より前、post は後、dev は post より前として扱う。

Node 側の semver.py と同じインターフェースを目指している:
  parse(s) -> Version | None
  normalize(spec) -> str | None
  classify(current, latest) -> 'latest'/'minor'/'major'/'unknown'
  pick_latest_minor_and_major(current, all_versions, overall_latest) -> tuple

PEP 440 における「minor up」「major up」の解釈:
  PEP 440 自体は major/minor の区別を強制しない (release segment は任意の
  数値列)。実用上は SemVer 慣例にならい release[0] を major、release[1] を
  minor とみなす。release が 1 要素しかない場合 minor=0 として扱う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# PEP 440 公式正規表現を簡略化したもの。dev/pre/post/local 部分は
# 比較に使う最低限の情報だけ抜く。
_VERSION_RE = re.compile(
    r"""^
    v?
    (?P<release>\d+(?:\.\d+)*)
    (?:
        (?P<pre_l>a|b|rc|alpha|beta|c|pre|preview)
        \.?
        (?P<pre_n>\d+)?
    )?
    (?:\.post(?P<post>\d+))?
    (?:\.dev(?P<dev>\d+))?
    (?:\+(?P<local>[a-zA-Z0-9.]+))?
    $
    """,
    re.VERBOSE,
)

_PRE_NORMALIZE = {
    'alpha': 'a', 'beta': 'b', 'c': 'rc', 'pre': 'rc', 'preview': 'rc',
}
_PRE_ORDER = {'a': 0, 'b': 1, 'rc': 2}


@dataclass(frozen=True)
class Version:
    raw: str
    release: tuple[int, ...]
    pre: Optional[tuple[str, int]] = None   # ('a'|'b'|'rc', n)
    post: Optional[int] = None
    dev: Optional[int] = None
    local: str = ''

    @property
    def major(self) -> int:
        return self.release[0] if self.release else 0

    @property
    def minor(self) -> int:
        return self.release[1] if len(self.release) >= 2 else 0

    @property
    def micro(self) -> int:
        return self.release[2] if len(self.release) >= 3 else 0

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None or self.dev is not None

    def _sort_key(self) -> tuple:
        # PEP 440 の順序: dev < pre < (release/post)。
        # release tuple を左寄せで揃え、修飾子は補助キーで決定する。
        release = tuple(self.release)
        # 修飾子なし = max(pre=(rc, ∞)), post=None, dev=None
        # 単純化: (release, has_pre_flag, pre_tuple, post, has_dev_flag, dev)
        pre_key = (0, *self.pre) if self.pre else (1,)
        post_key = self.post if self.post is not None else -1
        dev_key = self.dev if self.dev is not None else float('inf')
        return (release, pre_key, post_key, dev_key)

    def gt(self, other: 'Version') -> bool:
        return self._sort_key() > other._sort_key()

    def __lt__(self, other: 'Version') -> bool:
        return self._sort_key() < other._sort_key()


def parse(version: str | None) -> Version | None:
    if not version or not isinstance(version, str):
        return None
    s = version.strip()
    m = _VERSION_RE.match(s)
    if not m:
        return None
    release = tuple(int(x) for x in m.group('release').split('.'))
    pre = None
    if m.group('pre_l'):
        label = _PRE_NORMALIZE.get(m.group('pre_l'), m.group('pre_l'))
        n = int(m.group('pre_n')) if m.group('pre_n') else 0
        pre = (label, n)
    post = int(m.group('post')) if m.group('post') else None
    dev = int(m.group('dev')) if m.group('dev') else None
    local = m.group('local') or ''
    return Version(raw=s, release=release, pre=pre, post=post, dev=dev, local=local)


# version specifier ('>=1.0,<2.0' / '==1.2.*' / '~=1.4' / '^1.0' / '1.0') から
# 「現在採用されているバージョン」相当を抽出する。lock がない場合の妥協値。
_SPEC_SPLIT = re.compile(r'[,\s]+')


def normalize(raw: str | None) -> str | None:
    """PEP 440 版の `==X.Y.Z`/`>=X.Y.Z`/`~=X.Y` などから代表バージョンを抽出。

    現在版 (lockfile からの値) は素のバージョン文字列で渡されるのでそのまま通る。
    spec が複雑な場合は最初に現れる数値版を採用する (semver.normalize と同じ妥協)。
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s in ('*', 'any', 'latest'):
        return None
    # `==1.2.3`, `>=1.2.3`, `~=1.2`, `1.2.3` のいずれも数値版部分を拾う
    m = re.search(r'\d+(?:\.\d+)*(?:(?:a|b|c|rc|alpha|beta|pre|preview)\.?\d*)?'
                  r'(?:\.post\d+)?(?:\.dev\d+)?', s)
    return m.group(0) if m else None


def classify(current: str | None, latest: str | None) -> str:
    """'latest' | 'minor' | 'major' | 'unknown'

    cooldown により latest が current より古い版に rolled back される
    (= 「実は最新が install 済みだが、cutoff より新しいので候補集合から除外」)
    ケースでは、current >= latest として latest 扱いにする。そうしないと
    「現在版が最新 stable より新しい」のに minor の黄色行で警告されてしまう。
    """
    c, l = parse(current), parse(latest)
    if not c or not l:
        return 'unknown'
    if c.release == l.release and c.pre == l.pre and c.post == l.post:
        return 'latest'
    # current >= latest なら upgrade 不要 → latest 扱い
    if not c.__lt__(l):
        return 'latest'
    if l.major > c.major:
        return 'major'
    return 'minor'


def pick_latest_minor_and_major(
    current: str | None,
    all_versions: list[str],
    overall_latest: str | None,
) -> tuple[str | None, str | None]:
    """同 major 内の最新 (latestMinor) と、より上の major (latestMajor)。

    プレリリースは候補から除外する (PEP 440 慣例; pre は明示的 opt-in)。
    """
    cp = parse(current)
    if not cp:
        return None, None

    parsed: list[tuple[str, Version]] = []
    for v in all_versions:
        p = parse(v)
        if not p or p.is_prerelease:
            continue
        parsed.append((v, p))
    parsed.sort(key=lambda t: t[1], reverse=True)

    latest_minor: str | None = None
    for v, p in parsed:
        if p.major == cp.major and p.gt(cp):
            latest_minor = v
            break

    latest_major: str | None = None
    lp = parse(overall_latest)
    if lp and lp.major > cp.major:
        latest_major = overall_latest

    return latest_minor, latest_major


# ── PEP 440 specifier matcher (Section 4: Version specifiers) ──────────────
# 「requirements の規約を尊重した最高版」を計算するため。
# サポート: == ~= != >= <= > < / カンマで AND。
# 部分対応: ==X.Y.* (prefix wildcard)。`===` は exact 文字列一致として扱う。
# 未対応: arbitrary equality 以外の URL/local-version 細部、`!=X.*` の prefix 否定。
# pre-release は PEP 440 §6 簡易ルール: spec 内に pre-release 表記が無ければ除外。

_OP_TARGET_RE = re.compile(
    r'^\s*(?P<op>===|==|~=|!=|>=|<=|>|<)\s*(?P<target>\S+?)\s*$'
)


def _canonical_release(release: tuple[int, ...]) -> tuple[int, ...]:
    """PEP 440 == 比較用に release tuple の末尾の 0 を取り除く。

    (1, 0, 0) と (1,) は同じ release を表すとみなす。
    """
    out = list(release)
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return tuple(out)


def _exact_equal(v: 'Version', t: 'Version') -> bool:
    if _canonical_release(v.release) != _canonical_release(t.release):
        return False
    return v.pre == t.pre and v.post == t.post and v.dev == t.dev


def _matches_clause(v: 'Version', op: str, target_str: str) -> bool:
    """1 個の clause (op, target) の判定。target_str は `1.2.*` の wildcard も許容。"""
    # ==X.Y.* (prefix match) は op == '==' のときだけ有効
    if op == '==' and target_str.endswith('.*'):
        prefix_str = target_str[:-2]
        prefix = parse(prefix_str)
        if not prefix:
            return False
        plen = len(prefix.release)
        return v.release[:plen] == prefix.release

    target = parse(target_str)
    if not target:
        return False

    if op in ('==', '==='):
        return _exact_equal(v, target)
    if op == '!=':
        return not _exact_equal(v, target)
    if op == '~=':
        # ~=X.Y.Z は (>=X.Y.Z, <X.(Y+1)) と等価。最低 2 セグメント必要。
        if len(target.release) < 2:
            return False
        if v.__lt__(target):
            return False
        prefix = target.release[:-1]
        return v.release[:len(prefix)] == prefix
    if op == '>=':
        return not v.__lt__(target)
    if op == '<=':
        return v.__lt__(target) or _exact_equal(v, target)
    if op == '>':
        return target.__lt__(v) and not _exact_equal(v, target)
    if op == '<':
        return v.__lt__(target)
    return False


def matches_specifier(version: str | None, spec: str | None) -> bool:
    """version が spec を満たすか。spec は PEP 440 specifier (AND 結合は ',')。

    spec が空 or 未対応の場合 False を返す (false-negative 寄り)。
    pre-release 除外: spec 内に pre-release が無く v が pre-release なら False。
    """
    if not version or not spec:
        return False
    v = parse(version)
    if not v:
        return False
    clauses: list[tuple[str, str]] = []
    for chunk in spec.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _OP_TARGET_RE.match(chunk)
        if not m:
            return False  # 未対応な記法 (URL / local など) は match させない
        clauses.append((m.group('op'), m.group('target')))
    if not clauses:
        return False

    # Pre-release exclusion (PEP 440 §6 簡易): spec に pre が無ければ pre を弾く
    if v.is_prerelease:
        spec_has_pre = False
        for _, target_str in clauses:
            t = parse(target_str.removesuffix('.*'))
            if t and t.is_prerelease:
                spec_has_pre = True
                break
        if not spec_has_pre:
            return False

    return all(_matches_clause(v, op, target_str) for op, target_str in clauses)


def latest_matching(all_versions: list[str], spec: str | None) -> str | None:
    """spec を満たす最高安定版を返す。spec が空 / 全部 mismatch なら None。"""
    if not spec:
        return None
    parsed: list[tuple[str, Version]] = []
    for v in all_versions:
        if not matches_specifier(v, spec):
            continue
        p = parse(v)
        if p:
            parsed.append((v, p))
    if not parsed:
        return None
    parsed.sort(key=lambda t: t[1], reverse=True)
    return parsed[0][0]


def parseable(spec: str | None) -> bool:
    """spec が我々の matcher で扱える形式かどうか。

    None / 空文字は False を返す (= 「制約なし」とは別物として呼び出し側が扱う)。
    URL 参照や `1.0 || 2.0` のような未対応構文は False。
    operator + version の単純な AND (`,` 区切り) のみ True。
    """
    if not spec or not isinstance(spec, str):
        return False
    s = spec.strip()
    if not s or s in ('*', 'any', 'latest'):
        return False  # wildcard 系は別扱い (呼び出し側で「絶対最新」と解釈)
    for chunk in s.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _OP_TARGET_RE.match(chunk)
        if not m:
            return False
        # target が parse できるかも検証 (wildcard は ==X.Y.* のみ許す)
        target = m.group('target')
        if target.endswith('.*'):
            if m.group('op') != '==':
                return False
            if not parse(target[:-2]):
                return False
        else:
            if not parse(target):
                return False
    return True


def is_wildcard_spec(spec: str | None) -> bool:
    """`*` / `any` / `latest` / 空文字のように「全バージョン許可」を意味する spec か。"""
    if not spec or not isinstance(spec, str):
        return False
    return spec.strip() in ('*', 'any', 'latest')
