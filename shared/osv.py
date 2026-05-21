"""OSV.dev API クライアント。

ecosystem は呼び出し側から指定する: 'npm' (Node), 'PyPI' (Python) など。
OSV.dev の ecosystem ID は https://ossf.github.io/osv-schema/ の表参照。
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from typing import Callable

from . import debug_log, state

_UA = 'PkgUpdater/0.1'
_TIMEOUT = 25
# OSV.dev /v1/querybatch は 1000 件/リクエストが上限。安全側に分割する。
_BATCH_SIZE = 500

# 表示・ソート用の重要度順位。値が小さいほど深刻。
SEVERITY_ORDER: dict[str, int] = {
    'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'UNKNOWN': 4,
}
# GHSA は 'MODERATE' を使うので 'MEDIUM' に正規化する
_SEV_ALIAS = {'MODERATE': 'MEDIUM'}


def _severity_label(vuln: dict) -> str:
    """OSV 脆弱性レコードから CRITICAL/HIGH/MEDIUM/LOW ラベルを決定。

    優先順:
      1. severity[].score が数値として解釈可能なら CVSS スコア閾値で判定
      2. database_specific.severity の文字列を大文字化して採用 (GHSA は通常ここ)
      3. UNKNOWN
    閾値は npm audit / GitHub Advisories の慣例に合わせる (>=9 CRITICAL …)。
    """
    sev = vuln.get('severity') or []
    for s in sev:
        try:
            score = float((s or {}).get('score', ''))
        except (TypeError, ValueError):
            continue
        if score >= 9.0:
            return 'CRITICAL'
        if score >= 7.0:
            return 'HIGH'
        if score >= 4.0:
            return 'MEDIUM'
        return 'LOW'
    db = (vuln.get('database_specific') or {}).get('severity')
    if db:
        label = _SEV_ALIAS.get(str(db).upper(), str(db).upper())
        if label in SEVERITY_ORDER:
            return label
    return 'UNKNOWN'


def _primary_url(vuln: dict) -> str | None:
    refs = vuln.get('references') or []
    for r in refs:
        if r.get('type') in ('ADVISORY', 'WEB'):
            return r.get('url')
    if refs:
        return refs[0].get('url')
    return None


def _post_batch(queries: list[dict]) -> list[dict]:
    """単一バッチを POST。レスポンスの results を queries と同じ長さに揃えて返す。"""
    body = json.dumps({'queries': queries}).encode('utf-8')
    req = urllib.request.Request(
        state.get_osv_api_url(),
        data=body,
        headers={
            'User-Agent': _UA,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
        },
        method='POST',
    )
    proxy = state.get_proxy_url()
    t0 = time.monotonic()
    try:
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            )
            resp_cm = opener.open(req, timeout=_TIMEOUT)
        else:
            resp_cm = urllib.request.urlopen(req, timeout=_TIMEOUT)
        with resp_cm as resp:
            raw_body = resp.read()
            status = resp.status
            content_encoding = (resp.headers.get('Content-Encoding') or '').lower()
            duration_ms = int((time.monotonic() - t0) * 1000)
        if content_encoding == 'gzip':
            try:
                decoded = gzip.decompress(raw_body)
            except (OSError, EOFError):
                decoded = raw_body
        else:
            decoded = raw_body
        data = json.loads(decoded.decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        debug_log.log(
            'osv._post_batch',
            level='ERROR',
            summary=f'OSV.dev POST 失敗 ({duration_ms}ms) {len(queries)} queries: {type(e).__name__}',
            duration_ms=duration_ms, query_count=len(queries),
            error_type=type(e).__name__,
            detail={'url': state.get_osv_api_url(), 'error': str(e)},
        )
        return [{} for _ in queries]
    results = data.get('results') or []
    debug_log.log(
        'osv._post_batch',
        level='DEBUG',
        summary=f'OSV.dev POST {status} ({duration_ms}ms) {len(queries)} queries → {len(results)} results',
        status=status, duration_ms=duration_ms,
        query_count=len(queries), result_count=len(results),
        detail={'url': state.get_osv_api_url()},
    )
    if len(results) < len(queries):
        results = results + [{} for _ in range(len(queries) - len(results))]
    return results[:len(queries)]


def query_batch(
    packages: list[dict],
    on_progress: Callable[[int, int], None] | None = None,
    ecosystem: str = 'npm',
) -> list[dict]:
    """packages: [{name, version}, ...] → [{name, version, vulns: [...]}, ...]

    OSV.dev /v1/querybatch の上限 (1000 件) を避けるため内部で _BATCH_SIZE 件
    ごとに分割して POST する。on_progress(done, total) は各チャンク終了時に
    呼ばれる (UI から進捗表示するためのフック)。
    脆弱性が見つからなかったパッケージは結果に含めない。
    ecosystem は OSV.dev の ecosystem ID ('npm' / 'PyPI' / 'Go' …)。
    """
    valid = [p for p in packages if p.get('version')]
    if not valid:
        return []

    total = len(valid)
    out: list[dict] = []
    for start in range(0, total, _BATCH_SIZE):
        chunk = valid[start:start + _BATCH_SIZE]
        queries = [
            {'package': {'name': p['name'], 'ecosystem': ecosystem}, 'version': p['version']}
            for p in chunk
        ]
        results = _post_batch(queries)
        for pkg, res in zip(chunk, results):
            vulns_in = (res or {}).get('vulns') or []
            if not vulns_in:
                continue
            vulns_out = [{
                'id': v.get('id'),
                'summary': v.get('summary') or v.get('details', '')[:140],
                'severity': _severity_label(v),
                'url': _primary_url(v) or f'https://osv.dev/vulnerability/{v.get("id")}',
            } for v in vulns_in]
            vulns_out.sort(key=lambda x: SEVERITY_ORDER.get(x['severity'], 99))
            out.append({'name': pkg['name'], 'version': pkg['version'], 'vulns': vulns_out})
        if on_progress is not None:
            on_progress(min(start + _BATCH_SIZE, total), total)
    return out
