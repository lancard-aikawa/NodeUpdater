"""OSV.dev API クライアント (npm ecosystem)。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

_UA = 'NodeUpdater/0.1'
_API = 'https://api.osv.dev/v1/querybatch'
_TIMEOUT = 25


def _severity_label(vuln: dict) -> str:
    sev = vuln.get('severity') or []
    if sev:
        score = sev[0].get('score', '')
        if 'CVSS' in score:
            return 'cvss'
    db = (vuln.get('database_specific') or {}).get('severity')
    if db:
        return str(db).lower()
    return 'unknown'


def _primary_url(vuln: dict) -> str | None:
    refs = vuln.get('references') or []
    for r in refs:
        if r.get('type') in ('ADVISORY', 'WEB'):
            return r.get('url')
    if refs:
        return refs[0].get('url')
    return None


def query_batch(packages: list[dict]) -> list[dict]:
    """packages: [{name, version}, ...] → [{name, version, vulns: [...]}, ...]

    vulns の各要素は {id, summary, severity, url} の最小構造。
    """
    if not packages:
        return []

    queries = [
        {'package': {'name': p['name'], 'ecosystem': 'npm'}, 'version': p['version']}
        for p in packages if p.get('version')
    ]
    if not queries:
        return []

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
        return []

    results = data.get('results') or []
    out = []
    for pkg, res in zip([p for p in packages if p.get('version')], results):
        vulns_in = res.get('vulns') or []
        vulns_out = [{
            'id': v.get('id'),
            'summary': v.get('summary') or v.get('details', '')[:140],
            'severity': _severity_label(v),
            'url': _primary_url(v) or f'https://osv.dev/vulnerability/{v.get("id")}',
        } for v in vulns_in]
        if vulns_out:
            out.append({'name': pkg['name'], 'version': pkg['version'], 'vulns': vulns_out})
    return out
