"""GitHub Releases API クライアント (パッケージの release notes 取得)。

レート制限 (未認証 60 req/h) を避けるため (owner, repo) ごとに 24h キャッシュ。
GitHub Token を設定するか GITHUB_TOKEN 環境変数を設定すると 5000 req/h に拡張。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import cache, debug_log, state

_UA = 'PkgUpdater/0.1'
_API = 'https://api.github.com'
_TIMEOUT = 15
_CACHE_TTL = 24 * 60 * 60  # 24h
_DEFAULT_LIMIT = 30


def parse_repo_url(url: str | None) -> tuple[str, str] | None:
    """git+https://.../foo/bar.git や git@github.com:foo/bar.git から (owner, repo) を抽出。"""
    if not url or not isinstance(url, str):
        return None
    s = url.strip()
    # よくあるプレフィックスを剥がす
    for prefix in ('git+', 'git:', 'ssh://'):
        if s.startswith(prefix):
            s = s[len(prefix):]
    m = re.search(r'github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?(?:[/?#].*)?$', s)
    if not m:
        return None
    return m.group(1), m.group(2)


def _normalize_tag(tag: str | None) -> str | None:
    """'v1.2.3' / 'name@1.2.3' / '@scope/name@1.2.3' → '1.2.3' 部分。"""
    if not tag:
        return None
    t = tag.strip()
    if '@' in t:
        t = t[t.rfind('@') + 1:]
    if t.startswith('v') and len(t) > 1 and t[1].isdigit():
        t = t[1:]
    return t


def _open(req: urllib.request.Request, timeout: int):
    proxy = state.get_proxy_url()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _fetch_releases(owner: str, repo: str, limit: int) -> list[dict] | None:
    url = f'{_API}/repos/{owner}/{repo}/releases?per_page={limit}'
    headers = {
        'User-Agent': _UA,
        'Accept': 'application/vnd.github+json',
        'Accept-Encoding': 'gzip',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    token = os.environ.get('GITHUB_TOKEN') or state.get_github_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, headers=headers)
    short = f'{owner}/{repo}'
    t0 = time.monotonic()
    try:
        with _open(req, _TIMEOUT) as resp:
            status = resp.status
            raw_body = resp.read()
            if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
                try:
                    body = gzip.decompress(raw_body)
                except (OSError, EOFError):
                    body = raw_body
            else:
                body = raw_body
            duration_ms = int((time.monotonic() - t0) * 1000)
            if status != 200:
                # 401/403 = レート制限 / 認証問題、404 = repo 無し or releases 無し
                lv = 'WARN' if status in (401, 403) else 'INFO'
                debug_log.log(
                    'github_releases._fetch_releases',
                    level=lv,
                    summary=f'GitHub {status} ({duration_ms}ms) {short}',
                    status=status, duration_ms=duration_ms, token_present=bool(token),
                    detail={'url': url, 'body_head': body[:300].decode('utf-8', 'replace')},
                )
                return None
            try:
                data = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError as e:
                debug_log.log(
                    'github_releases._fetch_releases',
                    level='ERROR',
                    summary=f'GitHub JSON parse 失敗 ({duration_ms}ms) {short}',
                    duration_ms=duration_ms,
                    detail={'url': url, 'error': str(e)},
                )
                return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        debug_log.log(
            'github_releases._fetch_releases',
            level='ERROR',
            summary=f'GitHub 失敗 ({duration_ms}ms) {short}: {type(e).__name__}',
            duration_ms=duration_ms, error_type=type(e).__name__,
            detail={'url': url, 'error': str(e)},
        )
        return None
    debug_log.log(
        'github_releases._fetch_releases',
        level='DEBUG',
        summary=f'GitHub OK ({duration_ms}ms) {short}: {len(data)} releases',
        status=status, duration_ms=duration_ms, count=len(data),
        detail={'url': url},
    )
    return data


def fetch_releases_cached(owner: str, repo: str, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """[{tag_name, name, body, published_at, html_url, version}, ...] を返す。

    24h キャッシュ。GitHub Releases が存在しないリポジトリでは空リスト。
    """
    key = f'github_releases_{owner}_{repo}'
    cached = cache.load(key, _CACHE_TTL)
    if cached is not None:
        return cached.get('releases') or []

    fresh = _fetch_releases(owner, repo, limit)
    if fresh is None:
        # API エラー (レート制限 / 404 / ネットワーク) は空でキャッシュしない
        return []
    # 必要なフィールドだけ抽出してキャッシュサイズを抑える
    trimmed = []
    for r in fresh:
        trimmed.append({
            'tag_name': r.get('tag_name'),
            'name': r.get('name'),
            'body': r.get('body') or '',
            'published_at': r.get('published_at') or '',
            'html_url': r.get('html_url') or '',
            'prerelease': bool(r.get('prerelease')),
            'draft': bool(r.get('draft')),
            'version': _normalize_tag(r.get('tag_name')),
        })
    cache.save(key, {'releases': trimmed})
    return trimmed
