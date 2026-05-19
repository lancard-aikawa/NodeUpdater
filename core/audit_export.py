"""OSV / npm audit の結果を Markdown / CSV に書き出すヘルパ。"""
from __future__ import annotations

import csv
import io
from datetime import datetime


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


# ── OSV ───────────────────────────────────────────────────────────────────
def osv_to_text(data: dict, fmt: str, project_path: str = '') -> str:
    if fmt == 'csv':
        return _osv_to_csv(data, project_path)
    return _osv_to_markdown(data, project_path)


def _info_by_nv(scanned: list[dict]) -> dict[tuple, dict]:
    return {(d.get('name'), d.get('version')): d for d in scanned}


def _osv_kind(info: dict) -> str:
    if info.get('direct'):
        return '直接'
    roots = info.get('roots') or []
    if not roots:
        return '推移'
    head = ', '.join(roots[:5])
    if len(roots) > 5:
        head += f' (+{len(roots) - 5})'
    return f'推移 ← {head}'


def _osv_to_markdown(data: dict, project_path: str) -> str:
    results = data.get('results') or []
    scanned = data.get('scanned') or []
    source = data.get('source') or '(unknown)'
    direct_count = sum(1 for d in scanned if d.get('direct'))
    info_by_nv = _info_by_nv(scanned)

    lines: list[str] = []
    lines.append('# OSV Vulnerability Scan Report')
    lines.append('')
    if project_path:
        lines.append(f'- Project: `{project_path}`')
    lines.append(f'- Source: `{source}`')
    lines.append(f'- Scanned: {len(scanned)} packages (direct: {direct_count})')
    lines.append(f'- Vulnerable: {len(results)} packages')
    lines.append(f'- Generated: {_now_iso()}')
    lines.append('')

    if not results:
        lines.append('## Result')
        lines.append('')
        lines.append('脆弱性は検出されませんでした。')
        lines.append('')
        return '\n'.join(lines)

    lines.append('## Vulnerabilities')
    lines.append('')
    for r in results:
        info = info_by_nv.get((r.get('name'), r.get('version')), {})
        kind = _osv_kind(info)
        dev_tag = ' [dev]' if info.get('dev') else ''
        lines.append(f'### {r.get("name")}@{r.get("version")} ({kind}){dev_tag}')
        for v in r.get('vulns', []):
            sev = v.get('severity', 'UNKNOWN')
            vid = v.get('id', '?')
            summary = (v.get('summary') or '').replace('\n', ' ').strip()
            url = v.get('url', '')
            lines.append(f'- **[{sev}] {vid}**: {summary}')
            if url:
                lines.append(f'  {url}')
        lines.append('')
    return '\n'.join(lines)


def _osv_to_csv(data: dict, project_path: str) -> str:
    results = data.get('results') or []
    scanned = data.get('scanned') or []
    info_by_nv = _info_by_nv(scanned)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(
        ['package', 'version', 'kind', 'dev', 'roots', 'vuln_id', 'severity', 'summary', 'url']
    )
    for r in results:
        info = info_by_nv.get((r.get('name'), r.get('version')), {})
        kind = 'direct' if info.get('direct') else 'transitive'
        dev_flag = 'true' if info.get('dev') else 'false'
        roots = '|'.join(info.get('roots') or [])
        for v in r.get('vulns', []):
            writer.writerow([
                r.get('name', ''),
                r.get('version', ''),
                kind,
                dev_flag,
                roots,
                v.get('id', ''),
                v.get('severity', ''),
                (v.get('summary') or '').replace('\n', ' ').strip(),
                v.get('url', ''),
            ])
    return buf.getvalue()


# ── npm audit ─────────────────────────────────────────────────────────────
_NPM_SEV_ORDER = {'critical': 0, 'high': 1, 'moderate': 2, 'low': 3, 'info': 4}


def npm_audit_to_text(data: dict, fmt: str, project_path: str = '') -> str:
    if fmt == 'csv':
        return _npm_audit_to_csv(data, project_path)
    return _npm_audit_to_markdown(data, project_path)


def _fix_repr(info: dict) -> tuple[str, str, str]:
    """fix_available / fix_target / fix_is_major を表現用に整形して返す。"""
    fix = info.get('fixAvailable')
    if isinstance(fix, dict):
        return (
            'true',
            f'{fix.get("name", "")}@{fix.get("version", "")}',
            'true' if fix.get('isSemVerMajor') else 'false',
        )
    if fix is True:
        return 'true', '', ''
    return 'false', '', ''


def _npm_audit_to_markdown(data: dict, project_path: str) -> str:
    meta = (data.get('metadata') or {}).get('vulnerabilities') or {}
    vulns = data.get('vulnerabilities') or {}

    lines: list[str] = []
    lines.append('# npm audit Report')
    lines.append('')
    if project_path:
        lines.append(f'- Project: `{project_path}`')
    lines.append(
        f'- Total: {meta.get("total", 0)} '
        f'(critical: {meta.get("critical", 0)}, high: {meta.get("high", 0)}, '
        f'moderate: {meta.get("moderate", 0)}, low: {meta.get("low", 0)}, '
        f'info: {meta.get("info", 0)})'
    )
    lines.append(f'- Generated: {_now_iso()}')
    lines.append('')

    if data.get('error'):
        err = data['error']
        lines.append('## Error')
        lines.append('')
        lines.append(f'`{err.get("code", "?")}`: {err.get("summary", "")}')
        if err.get('detail'):
            lines.append('')
            lines.append('```')
            lines.append(err['detail'])
            lines.append('```')
        return '\n'.join(lines)

    if not vulns:
        lines.append('脆弱性は検出されませんでした。')
        lines.append('')
        return '\n'.join(lines)

    lines.append('## Vulnerabilities')
    lines.append('')
    items = sorted(vulns.items(), key=lambda kv: _NPM_SEV_ORDER.get(kv[1].get('severity', ''), 99))
    for name, info in items:
        sev = str(info.get('severity', 'unknown')).upper()
        rng = info.get('range', '') or ''
        direct = '直接' if info.get('isDirect') else '推移'
        fix_av, fix_target, fix_major = _fix_repr(info)
        if fix_av == 'true' and fix_target:
            fix_text = fix_target + (' (major)' if fix_major == 'true' else '')
        elif fix_av == 'true':
            fix_text = '可'
        else:
            fix_text = '不可'
        lines.append(f'### {name} [{sev}] ({direct})')
        lines.append(f'- range: `{rng}`')
        lines.append(f'- fix: {fix_text}')
        lines.append('')
    return '\n'.join(lines)


def _npm_audit_to_csv(data: dict, project_path: str) -> str:
    vulns = data.get('vulnerabilities') or {}
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(
        ['package', 'severity', 'kind', 'range', 'fix_available', 'fix_target', 'fix_is_major']
    )
    items = sorted(vulns.items(), key=lambda kv: _NPM_SEV_ORDER.get(kv[1].get('severity', ''), 99))
    for name, info in items:
        sev = str(info.get('severity', 'unknown'))
        rng = info.get('range', '') or ''
        kind = 'direct' if info.get('isDirect') else 'transitive'
        fix_av, fix_target, fix_major = _fix_repr(info)
        writer.writerow([name, sev, kind, rng, fix_av, fix_target, fix_major])
    return buf.getvalue()
