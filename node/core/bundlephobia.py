"""bundlephobia.com API クライアント (npm パッケージの bundle size 取得)。

特定 version の size は immutable なので結果を永続キャッシュする。
失敗時 (404 / 429 / タイムアウト) は静かに None を返し、UI 側はその
パッケージだけサイズ表示を空にする。
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from shared import cache, debug_log, state

_UA = 'NodeUpdater/0.1'
_BASE = 'https://bundlephobia.com/api/size'
_TIMEOUT = 20

_SIZE_CACHE_KEY = 'bundlephobia_sizes'
_SIZE_CACHE_TTL = 365 * 24 * 60 * 60  # 1 年 (immutable 前提)


def _open(req: urllib.request.Request, timeout: int):
    proxy = state.get_proxy_url()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_one(name: str, version: str | None) -> dict | None:
    """{size, gzip} を取得。analyzer が解析できないパッケージや 4xx/5xx は None。"""
    if not version:
        return None
    # workspace: / file: 等のローカル参照は対象外
    if any(version.startswith(p) for p in ('workspace:', 'file:', 'link:', 'git+', 'git:')):
        return None
    pkg = urllib.parse.quote(f'{name}@{version}', safe='@')
    url = f'{_BASE}?package={pkg}'
    short = f'{name}@{version}'
    req = urllib.request.Request(
        url, headers={
            'User-Agent': _UA, 'Accept': 'application/json', 'Accept-Encoding': 'gzip',
        },
    )
    t0 = time.monotonic()
    try:
        with _open(req, _TIMEOUT) as resp:
            status = resp.status
            raw_body = resp.read()
            if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
                try:
                    body = gzip.decompress(raw_body)
                except (OSError, EOFError):
                    body = raw_body  # 展開失敗時は raw を流して JSON パースに任せる
            else:
                body = raw_body
            duration_ms = int((time.monotonic() - t0) * 1000)
            if status != 200:
                debug_log.log(
                    'bundlephobia.fetch_one',
                    level='WARN',
                    summary=f'bundlephobia {status} ({duration_ms}ms) {short}',
                    status=status, duration_ms=duration_ms,
                    detail={'url': url, 'body_head': body[:300].decode('utf-8', 'replace')},
                )
                return None
            try:
                data = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError as e:
                debug_log.log(
                    'bundlephobia.fetch_one',
                    level='ERROR',
                    summary=f'bundlephobia JSON parse 失敗 ({duration_ms}ms) {short}',
                    duration_ms=duration_ms,
                    detail={'url': url, 'error': str(e)},
                )
                return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        debug_log.log(
            'bundlephobia.fetch_one',
            level='WARN',
            summary=f'bundlephobia 失敗 ({duration_ms}ms) {short}: {type(e).__name__}',
            duration_ms=duration_ms, error_type=type(e).__name__,
            detail={'url': url, 'error': str(e)},
        )
        return None
    debug_log.log(
        'bundlephobia.fetch_one',
        level='DEBUG',
        summary=f'bundlephobia OK ({duration_ms}ms) {short}: {data.get("size")} / gz {data.get("gzip")}',
        duration_ms=duration_ms,
        size=data.get('size'), gzip=data.get('gzip'),
        detail={'url': url},
    )
    return {'size': data.get('size'), 'gzip': data.get('gzip')}


def _fetch_many(
    packages: list[tuple[str, str]],
    max_workers: int | None = None,
    on_progress=None,
) -> dict[str, dict]:
    """packages: [(name, version), ...] → name@version → {size, gzip}"""
    out: dict[str, dict] = {}
    total = len(packages)
    if not packages:
        return out
    workers = max_workers if max_workers is not None else state.get_parallel_requests()
    # bundlephobia は per-request コストが大きいので並列度は抑える
    workers = min(workers, 4)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(fetch_one, name, ver): (name, ver) for name, ver in packages
        }
        done = 0
        for fut in as_completed(futures):
            name, ver = futures[fut]
            try:
                info = fut.result()
                if info:
                    out[f'{name}@{ver}'] = info
            except Exception:
                pass
            done += 1
            if on_progress:
                try:
                    on_progress(done, total)
                except Exception:
                    pass
    return out


def fetch_many_cached(
    packages: list[tuple[str, str | None]],
    on_progress=None,
) -> dict[str, dict]:
    """name → {size, gzip}。永続キャッシュにヒットしないものだけ API を叩く。"""
    sizes_cache: dict = cache.load(_SIZE_CACHE_KEY, _SIZE_CACHE_TTL) or {}
    out: dict[str, dict] = {}
    to_fetch: list[tuple[str, str]] = []
    for name, ver in packages:
        if not name or not ver:
            continue
        key = f'{name}@{ver}'
        hit = sizes_cache.get(key)
        if hit:
            out[name] = hit
        else:
            to_fetch.append((name, ver))

    if to_fetch:
        fresh = _fetch_many(to_fetch, on_progress=on_progress)
        for key, info in fresh.items():
            sizes_cache[key] = info
            # name 部分だけ取り出し (key = "name@version")
            at = key.rfind('@')
            name_only = key[:at] if at > 0 else key
            out[name_only] = info
        cache.save(_SIZE_CACHE_KEY, sizes_cache)
    elif on_progress:
        # 全件キャッシュヒットの場合も完了を通知
        on_progress(0, 0)

    return out
