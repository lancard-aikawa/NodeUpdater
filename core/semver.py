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
