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

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from shared import debug_log, state

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
    # gzip 圧縮を明示的に要求 (転送量を 5〜10x 削減)。
    req = urllib.request.Request(url, headers={
        'User-Agent': _UA,
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
    })
    short_url = _shorten_url(url)
    t0 = time.monotonic()
    try:
        with _open(req, timeout) as resp:
            status = resp.status
            raw_body = resp.read()
            if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
                try:
                    body = gzip.decompress(raw_body)
                except (OSError, EOFError) as e:
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    debug_log.log(
                        'pypi._http_get_json',
                        level='ERROR',
                        summary=f'gzip 展開失敗 ({duration_ms}ms) {short_url}',
                        duration_ms=duration_ms,
                        detail={'url': url, 'error': str(e)},
                    )
                    return None
            else:
                body = raw_body
            duration_ms = int((time.monotonic() - t0) * 1000)
            if status != 200:
                debug_log.log(
                    'pypi._http_get_json',
                    level='WARN',
                    summary=f'GET {status} ({duration_ms}ms) {short_url}',
                    status=status, duration_ms=duration_ms,
                    detail={'url': url, 'body_head': body[:500].decode('utf-8', 'replace')},
                )
                return None
            try:
                data = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError as e:
                debug_log.log(
                    'pypi._http_get_json',
                    level='ERROR',
                    summary=f'GET 200 JSON parse 失敗 ({duration_ms}ms) {short_url}',
                    status=status, duration_ms=duration_ms, error=str(e),
                    detail={'url': url, 'body_head': body[:500].decode('utf-8', 'replace')},
                )
                return None
            wire_size = len(raw_body)
            body_size = len(body)
            ratio = f' (gzip {wire_size / body_size:.2f}x)' if wire_size != body_size else ''
            debug_log.log(
                'pypi._http_get_json',
                level='DEBUG',
                summary=f'GET 200 ({duration_ms}ms, wire {wire_size:,}B / body {body_size:,}B{ratio}) {short_url}',
                status=status, duration_ms=duration_ms,
                wire_size=wire_size, body_size=body_size,
                detail={'url': url},
            )
            return data
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        debug_log.log(
            'pypi._http_get_json',
            level='ERROR',
            summary=f'GET 失敗 ({duration_ms}ms) {short_url}: {type(e).__name__}',
            duration_ms=duration_ms, error_type=type(e).__name__,
            detail={'url': url, 'error': str(e)},
        )
        return None


def _shorten_url(url: str) -> str:
    """ログ summary 用に長い registry URL を短縮 (ホスト + パス末尾)。"""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f'{p.netloc}{p.path[-80:]}'
    except (ValueError, TypeError):
        return url[:100]


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


def fetch_one(
    name: str,
    current_version: str | None,
    cooldown_days: int = 0,
    spec: str | None = None,
) -> dict:
    """単一パッケージの情報を取得して npm_registry.fetch_one と同形式の dict を返す。

    spec を渡すと「requirements の制約を満たす最高安定版」を allowedLatest に入れる
    (Install (within spec) ボタン用)。spec=None なら allowedLatest=None。
    """
    empty = {
        'pkgName': name, 'latest': None, 'latestMinor': None, 'latestMajor': None,
        'allowedLatest': None,
        'currentPublishedAt': None, 'latestPublishedAt': None,
        'latestMinorPublishedAt': None, 'latestMajorPublishedAt': None,
        'allowedLatestPublishedAt': None,
        'currentAgeInDays': None, 'latestMinorAgeInDays': None, 'latestMajorAgeInDays': None,
        'allowedLatestAgeInDays': None,
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

    # 「spec が許す最高版」: 3 系統に分岐する。
    #   1. spec が None / 空 / '*'/'any'/'latest' (wildcard) → 制約なしなので絶対最新
    #   2. spec が我々の matcher で解釈可能 → matches_specifier で絞った最高安定版
    #   3. 解釈不能 (`^1.0` / `1.0 || 2` / URL refs など A の範囲外) → '?' sentinel
    # '?' の場合 UI 側でツールチップに raw spec を出して「手動確認」を促す。
    if spec is None or pep440.is_wildcard_spec(spec):
        allowed_latest = latest
    elif pep440.parseable(spec):
        allowed_latest = pep440.latest_matching(eligible, spec)
    else:
        allowed_latest = '?'
    allowed_latest_published_at = (
        _first_upload_time(releases.get(allowed_latest))
        if allowed_latest and allowed_latest != '?'
        else None
    )

    deprecated = _yanked_reason(releases.get(current_version)) if current_version else None
    latest_deprecated = _yanked_reason(releases.get(latest)) if latest else None
    license_str = _extract_license(info)

    return {
        'pkgName': name,
        'latest': latest,
        'latestMinor': latest_minor,
        'latestMajor': latest_major,
        'allowedLatest': allowed_latest,
        'currentPublishedAt': current_published_at,
        'latestPublishedAt': latest_published_at,
        'latestMinorPublishedAt': latest_minor_published_at,
        'latestMajorPublishedAt': latest_major_published_at,
        'allowedLatestPublishedAt': allowed_latest_published_at,
        'currentAgeInDays': _age_in_days(current_published_at),
        'latestMinorAgeInDays': _age_in_days(latest_minor_published_at),
        'latestMajorAgeInDays': _age_in_days(latest_major_published_at),
        'allowedLatestAgeInDays': _age_in_days(allowed_latest_published_at),
        'provenance': None,  # PEP 740 attestations は未対応
        'deprecated': deprecated,
        'latestDeprecated': latest_deprecated,
        'license': license_str,
        'repositoryUrl': _extract_repo_url(info),
    }


def resolve_for_install(
    name: str,
    spec: str | None,
    cooldown_days: int,
) -> dict:
    """クールダウンインストール 用: spec と cooldown を考慮して install 予定版を決定する。

    フロー:
      1. PyPI JSON API から package metadata を取得
      2. cooldown を満たす版集合 (eligible) を作る
      3. spec が空/wildcard なら eligible の最高 stable
         spec が PEP 440 specifier として解釈可能なら eligible ∩ spec の最高
         spec が releases の key と完全一致するならその版を採用 (pre-release pin 等)
         それ以外は unsupported
      4. info.version (PyPI 最新) との差 (cooldown で弾かれた新版) を一覧化

    Returns dict: node 側 `npm_registry.resolve_for_install` と同じキー構成。
    """
    empty = {
        'name': name, 'found': False, 'resolved': None,
        'resolved_published_at': None, 'resolved_age_days': None,
        'raw_latest': None, 'raw_latest_published_at': None, 'raw_latest_age_days': None,
        'excluded_newer': [], 'spec_status': 'none', 'reason': None,
    }
    url = f'{state.get_pypi_index_url()}/{urllib.parse.quote(name)}/json'
    data = _http_get_json(url)
    if not data:
        empty['reason'] = f'PyPI に {name} が見つからない、または到達できませんでした'
        return empty

    info = data.get('info') or {}
    releases = data.get('releases') or {}
    raw_latest = info.get('version')

    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days) if cooldown_days > 0 else None
    all_versions = list(releases.keys())
    if cutoff is not None:
        eligible: list[str] = []
        for v in all_versions:
            ts = _first_upload_time(releases.get(v))
            if not _is_after(ts, cutoff):
                eligible.append(v)
    else:
        eligible = all_versions

    spec_clean = (spec or '').strip()
    if not spec_clean or pep440.is_wildcard_spec(spec_clean):
        spec_status = 'wildcard' if spec_clean else 'none'
        resolved = _highest_stable(eligible)
    elif spec_clean in releases:
        # PyPI に実在する exact version (pre-release pin 等) を許可
        spec_status = 'exact_pin'
        ts = _first_upload_time(releases.get(spec_clean))
        if cutoff is None or not _is_after(ts, cutoff):
            resolved = spec_clean
        else:
            resolved = None  # cooldown 未達なので install させない
    elif pep440.parseable(spec_clean):
        spec_status = 'parsed'
        resolved = pep440.latest_matching(eligible, spec_clean)
    else:
        spec_status = 'unsupported'
        resolved = None

    excluded_newer: list[dict] = []
    if cutoff is not None:
        excluded: list[tuple[str, str]] = []
        for v in all_versions:
            p = pep440.parse(v)
            if not p or p.is_prerelease:
                continue
            ts = _first_upload_time(releases.get(v))
            if _is_after(ts, cutoff):
                excluded.append((v, ts))
        excluded.sort(key=lambda t: t[1], reverse=True)
        for v, ts in excluded[:5]:
            excluded_newer.append({
                'version': v,
                'published_at': ts,
                'age_days': _age_in_days(ts),
            })

    raw_latest_ts = _first_upload_time(releases.get(raw_latest)) if raw_latest else None
    resolved_ts = _first_upload_time(releases.get(resolved)) if resolved else None

    reason: str | None = None
    if spec_status == 'unsupported':
        reason = f'spec "{spec_clean}" はクールダウンインストールでは未対応です (== / ~= / >= / <= / > / < / != のみ)'
    elif resolved is None and spec_status == 'exact_pin':
        reason = f'{spec_clean} は cooldown ({cooldown_days}日) を満たしていません'
    elif resolved is None:
        reason = f'cooldown {cooldown_days}日 を満たす版が見つかりませんでした'

    return {
        'name': name,
        'found': True,
        'resolved': resolved,
        'resolved_published_at': resolved_ts,
        'resolved_age_days': _age_in_days(resolved_ts),
        'raw_latest': raw_latest,
        'raw_latest_published_at': raw_latest_ts,
        'raw_latest_age_days': _age_in_days(raw_latest_ts),
        'excluded_newer': excluded_newer,
        'spec_status': spec_status,
        'reason': reason,
    }


def fetch_many(
    packages: list[tuple],
    max_workers: int | None = None,
    on_progress=None,
    cooldown_days: int = 0,
) -> dict[str, dict]:
    """[(name, current_version[, spec]), ...] を並列で問い合わせて name → info を返す。

    タプルは 2 要素 (name, version) でも 3 要素 (name, version, spec) でも可。
    spec を渡すと allowedLatest が計算される。
    """
    out: dict[str, dict] = {}
    total = len(packages)
    if not packages:
        return out
    workers = max_workers if max_workers is not None else state.get_parallel_requests()

    def _submit(ex, pkg):
        # 2-tuple/3-tuple 両対応 (Global タブは spec 無し)
        name = pkg[0]
        ver = pkg[1] if len(pkg) > 1 else None
        spec = pkg[2] if len(pkg) > 2 else None
        return ex.submit(fetch_one, name, ver, cooldown_days, spec)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {_submit(ex, p): p[0] for p in packages}
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
