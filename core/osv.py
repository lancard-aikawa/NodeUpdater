"""OSV.dev API クライアント (npm ecosystem)。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

_UA = 'NodeUpdater/0.1'
_API = 'https://api.osv.dev/v1/querybatch'
_TIMEOUT = 25

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
        _API,
        data=body,
        headers={'User-Agent': _UA, 'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return [{} for _ in queries]
    results = data.get('results') or []
    if len(results) < len(queries):
        results = results + [{} for _ in range(len(queries) - len(results))]
    return results[:len(queries)]


def query_batch(
    packages: list[dict],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """packages: [{name, version}, ...] → [{name, version, vulns: [...]}, ...]

    vulns の各要素は {id, summary, severity, url} の最小構造。
    脆弱性が見つからなかったパッケージは結果に含めない。
    """
    valid = [p for p in packages if p.get('version')]
    if not valid:
        return []

    queries = [
        {'package': {'name': p['name'], 'ecosystem': 'npm'}, 'version': p['version']}
        for p in valid
    ]
    results = _post_batch(queries)

    out = []
    for pkg, res in zip(valid, results):
        vulns_in = (res or {}).get('vulns') or []
        if not vulns_in:
            continue
        vulns_out = [{
            'id': v.get('id'),
            'summary': v.get('summary') or v.get('details', '')[:140],
            'severity': _severity_label(v),
            'url': _primary_url(v) or f'https://osv.dev/vulnerability/{v.get("id")}',
        } for v in vulns_in]
        # パッケージ内では severity の重い順に並べる
        vulns_out.sort(key=lambda x: SEVERITY_ORDER.get(x['severity'], 99))
        out.append({'name': pkg['name'], 'version': pkg['version'], 'vulns': vulns_out})
    if on_progress is not None:
        on_progress(len(valid), len(valid))
    return out
