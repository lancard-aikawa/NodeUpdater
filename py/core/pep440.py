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
    """'latest' | 'minor' | 'major' | 'unknown'"""
    c, l = parse(current), parse(latest)
    if not c or not l:
        return 'unknown'
    if c.release == l.release and c.pre == l.pre and c.post == l.post:
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
