"""registry.npmjs.org への問い合わせ。標準ライブラリ (urllib) のみ。"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from shared import state

from . import semver

_UA = 'NodeUpdater/0.1'
_TIMEOUT = 10


def _encode_pkg(name: str) -> str:
    # @scope/pkg → @scope%2Fpkg
    return name.replace('@', '%40', 1).replace('/', '%2F')


def _open(req: urllib.request.Request, timeout: int):
    """proxy 設定があればそれを経由して開く。"""
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
    """iso_ts が cutoff より後 (= 新しすぎる) なら True。"""
    if not iso_ts:
        return False
    try:
        return datetime.fromisoformat(iso_ts.replace('Z', '+00:00')) > cutoff
    except (ValueError, TypeError):
        return False


def _highest_stable(versions: list[str]) -> str | None:
    parsed = [(v, semver.parse(v)) for v in versions if v and '-' not in v]
    parsed = [(v, p) for v, p in parsed if p]
    if not parsed:
        return None
    parsed.sort(key=lambda t: (t[1].major, t[1].minor, t[1].patch), reverse=True)
    return parsed[0][0]


def _extract_repo_url(data: dict | None) -> str | None:
    """npm registry の repository フィールドから URL を抽出。"""
    if not isinstance(data, dict):
        return None
    repo = data.get('repository')
    if isinstance(repo, str):
        return repo
    if isinstance(repo, dict):
        return repo.get('url')
    return None


def _license_label(version_obj: dict | None, fallback: dict | None) -> str | None:
    """package metadata から SPDX 識別子相当の文字列を抽出。

    version 単位 → top-level の順で探し、{type, url} 形式は type を採用する。
    """
    for src in (version_obj or {}, fallback or {}):
        lic = src.get('license') if isinstance(src, dict) else None
        if isinstance(lic, str) and lic.strip():
            return lic.strip()
        if isinstance(lic, dict) and isinstance(lic.get('type'), str):
            return lic['type'].strip()
        # 古い形式 (licenses: [{type, url}, ...])
        licenses = src.get('licenses') if isinstance(src, dict) else None
        if isinstance(licenses, list):
            types = [l.get('type') for l in licenses if isinstance(l, dict) and l.get('type')]
            if types:
                return ' OR '.join(types)
    return None


def fetch_one(
    name: str,
    current_version: str | None,
    cooldown_days: int = 0,
    spec: str | None = None,
) -> dict:
    """単一パッケージの情報を取得して npmChecker.js と同形式の dict を返す。

    spec を渡すと「package.json の規約 (^/~/range 等) を満たす最高安定版」を
    allowedLatest に入れる。spec=None / wildcard なら絶対最新を、解釈不能なら
    '?' sentinel を返す (UI 側で raw spec をツールチップ表示)。
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
    data = _http_get_json(f'{state.get_registry_url()}/{_encode_pkg(name)}')
    if not data:
        return empty

    raw_latest = (data.get('dist-tags') or {}).get('latest')
    time_map = data.get('time') or {}
    versions = data.get('versions') or {}

    current_published_at = time_map.get(current_version) if current_version else None

    version_data = versions.get(current_version) if current_version else None
    provenance = bool((version_data or {}).get('dist', {}).get('attestations')) if version_data is not None else None

    # 供給チェーンバッファ: cutoff より新しい版は候補集合から除外。
    # current_version はユーザーが既に使っているので除外対象外 (age 表示のため必要)。
    all_versions = list(versions.keys())
    if cooldown_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
        eligible = [v for v in all_versions if not _is_after(time_map.get(v), cutoff)]
    else:
        eligible = all_versions

    # cooldown により dist-tags.latest が除外されたら、cutoff 以前の最高 stable を採用
    if raw_latest and raw_latest in eligible:
        latest = raw_latest
    else:
        latest = _highest_stable(eligible) or raw_latest

    latest_published_at = time_map.get(latest) if latest else None
    latest_minor, latest_major = semver.pick_latest_minor_and_major(current_version, eligible, latest)

    latest_minor_published_at = time_map.get(latest_minor) if latest_minor else None
    latest_major_published_at = time_map.get(latest_major) if latest_major else None

    # deprecated: 文字列の deprecation メッセージ。空文字も None 扱いに統一する。
    def _dep(v: str | None) -> str | None:
        if not v:
            return None
        msg = (versions.get(v) or {}).get('deprecated')
        return msg.strip() if isinstance(msg, str) and msg.strip() else None

    deprecated = _dep(current_version)
    latest_deprecated = _dep(latest)
    license_str = _license_label(version_data, data)

    # 「spec が許す最高版」(Wanted): 3 系統に分岐。
    #   1. spec が None / 空 / wildcard (`*`/`x`/`latest`) → 制約なし → 絶対最新
    #   2. parseable (^/~/range など A の範囲内)           → matches_spec で絞った最高
    #   3. それ以外 (URL refs / `||` / hyphen range など)   → '?' sentinel
    if spec is None or semver.is_wildcard_spec(spec):
        allowed_latest = latest
    elif semver.parseable_spec(spec):
        allowed_latest = semver.latest_matching(eligible, spec)
    else:
        allowed_latest = '?'
    allowed_latest_published_at = (
        time_map.get(allowed_latest)
        if allowed_latest and allowed_latest != '?'
        else None
    )

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
        'provenance': provenance,
        'deprecated': deprecated,
        'latestDeprecated': latest_deprecated,
        'license': license_str,
        'repositoryUrl': _extract_repo_url(data),
    }


def fetch_many(
    packages: list[tuple],
    max_workers: int | None = None,
    on_progress=None,
    cooldown_days: int = 0,
) -> dict[str, dict]:
    """[(name, current_version[, spec]), ...] を並列で問い合わせて name → info を返す。

    タプルは 2 要素 (name, version) でも 3 要素 (name, version, spec) でも可。
    spec を渡すと Wanted (allowedLatest) が計算される。
    on_progress(done, total) が指定されていれば各完了時に呼ぶ（別スレッドから）。
    cooldown_days > 0 のとき、その日数以内に公開された版は候補から除外する。
    """
    out: dict[str, dict] = {}
    total = len(packages)
    if not packages:
        return out
    workers = max_workers if max_workers is not None else state.get_parallel_requests()

    def _submit(ex, pkg):
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


def resolve_for_install(
    name: str,
    spec: str | None,
    cooldown_days: int,
) -> dict:
    """Safe Install 用: spec と cooldown を考慮して install 予定版を決定する。

    フロー:
      1. registry から package metadata を取得
      2. cooldown を満たす版集合 (eligible) を作る
      3. spec が空/wildcard なら eligible の最高 stable
         spec が我々の matcher で扱える形式なら eligible ∩ spec の最高
         spec が time_map のキーと完全一致するならその版を採用 (prerelease pin 等)
         それ以外は unsupported
      4. dist-tags.latest との差 (cooldown で弾かれた新版) を一覧化して返す

    Returns dict:
      found: bool                — registry に存在したか
      resolved: str | None       — install 予定版 (None = 失敗)
      resolved_published_at: str | None
      resolved_age_days: int | None
      raw_latest: str | None     — registry の dist-tags.latest (cooldown 適用前)
      raw_latest_published_at: str | None
      raw_latest_age_days: int | None
      excluded_newer: list[dict] — cutoff より新しい安定版 (新しい順, 最大 5)
                                    各要素 {version, published_at, age_days}
      spec_status: 'none'|'wildcard'|'parsed'|'exact_pin'|'unsupported'
      reason: str | None         — UI に出す簡易メッセージ
    """
    empty = {
        'name': name, 'found': False, 'resolved': None,
        'resolved_published_at': None, 'resolved_age_days': None,
        'raw_latest': None, 'raw_latest_published_at': None, 'raw_latest_age_days': None,
        'excluded_newer': [], 'spec_status': 'none', 'reason': None,
    }
    data = _http_get_json(f'{state.get_registry_url()}/{_encode_pkg(name)}')
    if not data:
        empty['reason'] = 'registry にアクセスできませんでした'
        return empty
    # 404 の場合 data は None で上で抜ける。ここに来た時点で取得は成功。
    if not (data.get('versions') or {}) and not (data.get('dist-tags') or {}):
        empty['reason'] = f'パッケージ {name} は registry に存在しません'
        return empty

    versions = data.get('versions') or {}
    time_map = data.get('time') or {}
    raw_latest = (data.get('dist-tags') or {}).get('latest')

    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days) if cooldown_days > 0 else None
    all_versions = list(versions.keys())
    if cutoff is not None:
        eligible = [v for v in all_versions if not _is_after(time_map.get(v), cutoff)]
    else:
        eligible = all_versions

    # spec の解釈
    spec_clean = (spec or '').strip()
    if not spec_clean or semver.is_wildcard_spec(spec_clean):
        spec_status = 'wildcard' if spec_clean else 'none'
        resolved = _highest_stable(eligible)
    elif spec_clean in time_map:
        # registry に実在する exact version (prerelease pin など) を許可
        spec_status = 'exact_pin'
        if cutoff is None or not _is_after(time_map.get(spec_clean), cutoff):
            resolved = spec_clean
        else:
            resolved = None  # cooldown 未満なので install させない
    elif semver.parseable_spec(spec_clean):
        spec_status = 'parsed'
        resolved = semver.latest_matching(eligible, spec_clean)
    else:
        spec_status = 'unsupported'
        resolved = None

    # cutoff より新しい安定版 = cooldown により除外された候補
    excluded_newer: list[dict] = []
    if cutoff is not None:
        excluded: list[tuple[str, str]] = []
        for v in all_versions:
            if '-' in v:
                continue
            ts = time_map.get(v)
            if _is_after(ts, cutoff):
                excluded.append((v, ts))
        excluded.sort(key=lambda t: t[1], reverse=True)
        for v, ts in excluded[:5]:
            excluded_newer.append({
                'version': v,
                'published_at': ts,
                'age_days': _age_in_days(ts),
            })

    raw_latest_ts = time_map.get(raw_latest) if raw_latest else None
    resolved_ts = time_map.get(resolved) if resolved else None

    reason: str | None = None
    if spec_status == 'unsupported':
        reason = f'spec "{spec_clean}" は Safe Install では未対応です (`^` / `~` / 範囲 / 完全一致版のみ)'
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


def search(query: str, size: int = 10) -> list[dict]:
    """`/-/v1/search` でパッケージ検索。"""
    q = urllib.parse.quote(query)
    data = _http_get_json(f'{state.get_registry_url()}/-/v1/search?text={q}&size={size}', timeout=8)
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
    data = _http_get_json(f'{state.get_registry_url()}/{_encode_pkg(name)}', timeout=15)
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
