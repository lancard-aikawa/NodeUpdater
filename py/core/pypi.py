"""pypi.org JSON API クライアント。標準ライブラリ (urllib) のみ。

PyPI の JSON 仕様: https://warehouse.pypa.io/api-reference/json.html

  GET https://pypi.org/pypi/<name>/json
    → {info: {...}, releases: {"1.0": [{upload_time_iso_8601, yanked, ...}], ...}}

Node 側の npm_registry.py と同形式の dict を返す:
  pkgName, latest, latestMinor, latestMajor,
  currentPublishedAt, latestPublishedAt, latestMinorPublishedAt, latestMajorPublishedAt,
  currentAgeInDays, latestMinorAgeInDays, latestMajorAgeInDays,
  provenance (=None: PyPI は PEP 740 対応中、ここでは未取得),
  deprecated (=yanked_reason of current), latestDeprecated (=yanked_reason of latest),
  license, repositoryUrl
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from shared import state

from . import pep440

_UA = 'PypkgUpdater/0.1'
_TIMEOUT = 10


def _open(req: urllib.request.Request, timeout: int):
    proxy = state.get_proxy_url()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _http_get_json(url: str, timeout: int = _TIMEOUT) -> dict | None:
    req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
    try:
        with _open(req, timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _age_in_days(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace('Z', '+00:00'))
        return int((datetime.now(timezone.utc) - dt).total_seconds() // 86400)
    except (ValueError, TypeError):
        return None


def _is_after(iso_ts: str | None, cutoff: datetime) -> bool:
    if not iso_ts:
        return False
    try:
        return datetime.fromisoformat(iso_ts.replace('Z', '+00:00')) > cutoff
    except (ValueError, TypeError):
        return False


def _highest_stable(versions: list[str]) -> str | None:
    """安定版のみで最高 version を返す (PEP 440 pre/dev を除外)。"""
    parsed: list[tuple[str, pep440.Version]] = []
    for v in versions:
        p = pep440.parse(v)
        if p and not p.is_prerelease:
            parsed.append((v, p))
    if not parsed:
        return None
    parsed.sort(key=lambda t: t[1], reverse=True)
    return parsed[0][0]


def _first_upload_time(release_files: list[dict] | None) -> str | None:
    """release[version] は files の list。1 件目の upload_time を採用。"""
    if not release_files:
        return None
    for f in release_files:
        ts = f.get('upload_time_iso_8601') or f.get('upload_time')
        if ts:
            return ts
    return None


def _yanked_reason(release_files: list[dict] | None) -> str | None:
    """その version が yanked なら理由文字列、そうでなければ None。"""
    if not release_files:
        return None
    # files は通常同じ yanked 状態。最初のエントリを採用。
    f = release_files[0] or {}
    if not f.get('yanked'):
        return None
    return (f.get('yanked_reason') or 'yanked').strip()


def _extract_license(info: dict) -> str | None:
    """info.license が空なら classifiers から OSI 表記を拾う。"""
    lic = (info.get('license') or '').strip()
    if lic and len(lic) <= 80:  # SPDX 短い識別子 / 簡潔な文字列を優先
        return lic
    for clf in info.get('classifiers') or []:
        if isinstance(clf, str) and clf.startswith('License ::'):
            # "License :: OSI Approved :: MIT License" → "MIT License"
            parts = [p.strip() for p in clf.split('::')]
            return parts[-1] if parts else None
    return lic or None


def _extract_repo_url(info: dict) -> str | None:
    """project_urls から Source/Repository/Homepage を優先順で探す。"""
    urls = info.get('project_urls') or {}
    if not isinstance(urls, dict):
        return None
    # PyPI は label 大文字小文字統一なし。よくあるキーを優先順で見る。
    preferred = ['Source', 'Source Code', 'Repository', 'Homepage', 'Home']
    lower_map = {k.lower(): v for k, v in urls.items() if isinstance(k, str)}
    for key in preferred:
        v = lower_map.get(key.lower())
        if isinstance(v, str) and v.strip():
            return v.strip()
    # フォールバック: 何か github を含む URL があればそれ
    for v in urls.values():
        if isinstance(v, str) and 'github.com' in v:
            return v
    return info.get('home_page') or None


def fetch_one(name: str, current_version: str | None, cooldown_days: int = 0) -> dict:
    """単一パッケージの情報を取得して npm_registry.fetch_one と同形式の dict を返す。"""
    empty = {
        'pkgName': name, 'latest': None, 'latestMinor': None, 'latestMajor': None,
        'currentPublishedAt': None, 'latestPublishedAt': None,
        'latestMinorPublishedAt': None, 'latestMajorPublishedAt': None,
        'currentAgeInDays': None, 'latestMinorAgeInDays': None, 'latestMajorAgeInDays': None,
        'provenance': None,
        'deprecated': None, 'latestDeprecated': None, 'license': None,
        'repositoryUrl': None,
    }
    # PyPI の package 名は URL-safe。スコープ名 (@scope/name) は存在しない。
    url = f'{state.get_pypi_index_url()}/{urllib.parse.quote(name)}/json'
    data = _http_get_json(url)
    if not data:
        return empty

    info = data.get('info') or {}
    releases = data.get('releases') or {}

    raw_latest = info.get('version')  # PyPI 上の最新 (yanked 除外済み)
    current_published_at = _first_upload_time(releases.get(current_version)) if current_version else None

    all_versions = list(releases.keys())
    if cooldown_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
        eligible: list[str] = []
        for v in all_versions:
            ts = _first_upload_time(releases.get(v))
            if not _is_after(ts, cutoff):
                eligible.append(v)
    else:
        eligible = all_versions

    if raw_latest and raw_latest in eligible:
        latest = raw_latest
    else:
        latest = _highest_stable(eligible) or raw_latest

    latest_published_at = _first_upload_time(releases.get(latest)) if latest else None
    latest_minor, latest_major = pep440.pick_latest_minor_and_major(
        current_version, eligible, latest,
    )

    latest_minor_published_at = (
        _first_upload_time(releases.get(latest_minor)) if latest_minor else None
    )
    latest_major_published_at = (
        _first_upload_time(releases.get(latest_major)) if latest_major else None
    )

    deprecated = _yanked_reason(releases.get(current_version)) if current_version else None
    latest_deprecated = _yanked_reason(releases.get(latest)) if latest else None
    license_str = _extract_license(info)

    return {
        'pkgName': name,
        'latest': latest,
        'latestMinor': latest_minor,
        'latestMajor': latest_major,
        'currentPublishedAt': current_published_at,
        'latestPublishedAt': latest_published_at,
        'latestMinorPublishedAt': latest_minor_published_at,
        'latestMajorPublishedAt': latest_major_published_at,
        'currentAgeInDays': _age_in_days(current_published_at),
        'latestMinorAgeInDays': _age_in_days(latest_minor_published_at),
        'latestMajorAgeInDays': _age_in_days(latest_major_published_at),
        'provenance': None,  # PEP 740 attestations は未対応
        'deprecated': deprecated,
        'latestDeprecated': latest_deprecated,
        'license': license_str,
        'repositoryUrl': _extract_repo_url(info),
    }


def fetch_many(
    packages: list[tuple[str, str | None]],
    max_workers: int | None = None,
    on_progress=None,
    cooldown_days: int = 0,
) -> dict[str, dict]:
    """[(name, current_version), ...] を並列で問い合わせて name → info を返す。"""
    out: dict[str, dict] = {}
    total = len(packages)
    if not packages:
        return out
    workers = max_workers if max_workers is not None else state.get_parallel_requests()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_one, name, ver, cooldown_days): name for name, ver in packages}
        done = 0
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception:
                out[name] = {'pkgName': name, 'latest': None}
            done += 1
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception:
                    pass
    return out
