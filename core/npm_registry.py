"""registry.npmjs.org への問い合わせ。標準ライブラリ (urllib) のみ。"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import semver

_UA = 'NodeUpdater/0.1'
_TIMEOUT = 10
_REGISTRY = 'https://registry.npmjs.org'


def _encode_pkg(name: str) -> str:
    # @scope/pkg → @scope%2Fpkg
    return name.replace('@', '%40', 1).replace('/', '%2F')


def _http_get_json(url: str, timeout: int = _TIMEOUT) -> dict | None:
    req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def fetch_one(name: str, current_version: str | None) -> dict:
    """単一パッケージの情報を取得して npmChecker.js と同形式の dict を返す。"""
    empty = {
        'pkgName': name, 'latest': None, 'latestMinor': None, 'latestMajor': None,
        'currentPublishedAt': None, 'latestPublishedAt': None,
        'latestMinorPublishedAt': None, 'latestMajorPublishedAt': None,
        'currentAgeInDays': None, 'latestMinorAgeInDays': None, 'latestMajorAgeInDays': None,
        'provenance': None,
    }
    data = _http_get_json(f'{_REGISTRY}/{_encode_pkg(name)}')
    if not data:
        return empty

    latest = (data.get('dist-tags') or {}).get('latest')
    time_map = data.get('time') or {}
    versions = data.get('versions') or {}

    latest_published_at = time_map.get(latest) if latest else None
    current_published_at = time_map.get(current_version) if current_version else None

    version_data = versions.get(current_version) if current_version else None
    provenance = bool((version_data or {}).get('dist', {}).get('attestations')) if version_data is not None else None

    all_versions = list(versions.keys())
    latest_minor, latest_major = semver.pick_latest_minor_and_major(current_version, all_versions, latest)

    latest_minor_published_at = time_map.get(latest_minor) if latest_minor else None
    latest_major_published_at = time_map.get(latest_major) if latest_major else None

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
        'provenance': provenance,
    }


def fetch_many(
    packages: list[tuple[str, str | None]],
    max_workers: int = 8,
    on_progress=None,
) -> dict[str, dict]:
    """[(name, current_version), ...] を並列で問い合わせて name → info を返す。

    on_progress(done, total) が指定されていれば各完了時に呼ぶ（別スレッドから）。
    """
    out: dict[str, dict] = {}
    total = len(packages)
    if not packages:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_one, name, ver): name for name, ver in packages}
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


def search(query: str, size: int = 10) -> list[dict]:
    """`/-/v1/search` でパッケージ検索。"""
    q = urllib.parse.quote(query)
    data = _http_get_json(f'{_REGISTRY}/-/v1/search?text={q}&size={size}', timeout=8)
    if not data:
        return []
    out = []
    for obj in data.get('objects', []):
        p = obj.get('package', {})
        out.append({
            'name': p.get('name'),
            'version': p.get('version'),
            'description': p.get('description', ''),
            'npmUrl': (p.get('links') or {}).get('npm') or f'https://www.npmjs.com/package/{p.get("name")}',
        })
    return out


def fetch_versions(name: str, limit: int = 20) -> dict:
    """全バージョン詳細（公開日・age・provenance）を新しい順に返す。"""
    empty = {'name': name, 'description': '', 'latest': None, 'versions': [], 'npmUrl': ''}
    data = _http_get_json(f'{_REGISTRY}/{_encode_pkg(name)}', timeout=15)
    if not data:
        return empty

    time_map = data.get('time') or {}
    versions_obj = data.get('versions') or {}
    latest = (data.get('dist-tags') or {}).get('latest')

    candidates = [v for v in versions_obj.keys() if v in time_map and '-' not in v]
    candidates.sort(key=lambda v: time_map.get(v, ''), reverse=True)

    result_versions = []
    for v in candidates[:limit]:
        published_at = time_map.get(v)
        result_versions.append({
            'version': v,
            'publishedAt': published_at,
            'ageInDays': _age_in_days(published_at),
            'provenance': bool((versions_obj.get(v) or {}).get('dist', {}).get('attestations')),
            'isLatest': v == latest,
        })

    return {
        'name': name,
        'description': data.get('description', ''),
        'latest': latest,
        'versions': result_versions,
        'npmUrl': f'https://www.npmjs.com/package/{name}',
    }
