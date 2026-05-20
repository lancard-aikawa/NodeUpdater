"""PypkgUpdater メインウィンドウ。

node 側 ui/app.py を参考にした最小版。
Project / Global / Audit の 3 タブのみ、フィルタ + Cooldown + Recent ドロップダウン付き。
インストール実行や履歴は将来追加 (まずは表示/スキャン主体)。
"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from shared import cache, osv, state, ui_tabs

from py.core import pep440, pip_global, pypi, pyproject

from .table import PackageTable

_CACHE_TTL = 24 * 60 * 60
_OSV_CACHE_TTL = 12 * 60 * 60


class App(tk.Tk):
    def __init__(self, initial_project: Path | None = None):
        super().__init__()
        self.title('PypkgUpdater')
        self.geometry('1100x640')
        self.minsize(820, 480)

        self._build_layout()
        self._last_osv: dict | None = None

        if initial_project:
            self.current_project = initial_project
            self._set_recent_and_select(str(initial_project))
            self.notebook.select(self.tab_project)
            self.after(100, self.refresh_project)
        else:
            self.current_project = None
            self.notebook.select(self.tab_global)
            self.after(100, self.refresh_global)

    # ── レイアウト ────────────────────────────────────────────────────────────
    def _build_layout(self) -> None:
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill='x')
        ttk.Label(top, text='Project:').pack(side='left')
        self.project_combo = ttk.Combobox(top, state='readonly', width=70)
        self.project_combo['values'] = state.load_recent_projects(predicate=_is_py_project)
        self.project_combo.pack(side='left', padx=(4, 8))
        self.project_combo.bind('<<ComboboxSelected>>', self._on_recent_selected)
        self.choose_btn = ttk.Button(top, text='Choose…', command=self.choose_project)
        self.choose_btn.pack(side='left')

        ttk.Label(top, text='  Cooldown:').pack(side='left', padx=(12, 2))
        self.cooldown_var = tk.IntVar(value=state.get_cooldown_days())
        self.cooldown_spin = ttk.Spinbox(
            top, from_=0, to=90, width=4,
            textvariable=self.cooldown_var,
            command=self._on_cooldown_changed,
        )
        self.cooldown_spin.pack(side='left')
        ttk.Label(top, text='日').pack(side='left', padx=(2, 0))

        self.status_label = ttk.Label(top, text='', foreground='#0a6')
        self.status_label.pack(side='right')
        self.progress = ttk.Progressbar(top, mode='indeterminate', length=140)
        self._busy = False
        self._action_buttons: list[ttk.Button] = [self.choose_btn]

        self.notebook = ttk.Notebook(self, style=ui_tabs.ensure_notebook_style())
        self.notebook.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        # Global tab (左端: argv なし時のデフォルト)
        self.tab_global = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_global, text='Global (pip list)')
        gbar = ttk.Frame(self.tab_global, padding=(4, 4))
        gbar.pack(fill='x')
        gb1 = ttk.Button(gbar, text='Refresh', command=self.refresh_global)
        gb1.pack(side='left')
        gb2 = ttk.Button(gbar, text='Force Refresh',
                         command=lambda: self.refresh_global(force=True))
        gb2.pack(side='left', padx=(4, 0))
        gb3 = ttk.Button(gbar, text='Open on PyPI',
                         command=lambda: self._open_selected_pypi('global'))
        gb3.pack(side='left', padx=(12, 0))
        self.global_table = PackageTable(self.tab_global)
        self._make_filter_bar(self.tab_global, self.global_table)
        self.global_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Project tab
        self.tab_project = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_project, text='Project')
        pbar = ttk.Frame(self.tab_project, padding=(4, 4))
        pbar.pack(fill='x')
        pb1 = ttk.Button(pbar, text='Refresh', command=self.refresh_project)
        pb1.pack(side='left')
        pb2 = ttk.Button(pbar, text='Force Refresh',
                         command=lambda: self.refresh_project(force=True))
        pb2.pack(side='left', padx=(4, 0))
        pb3 = ttk.Button(pbar, text='Open on PyPI',
                         command=lambda: self._open_selected_pypi('project'))
        pb3.pack(side='left', padx=(12, 0))
        self.project_table = PackageTable(self.tab_project)
        self._make_filter_bar(self.tab_project, self.project_table)
        self.project_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Audit tab (OSV PyPI ecosystem)
        self.tab_audit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_audit, text='Audit (OSV)')
        abar = ttk.Frame(self.tab_audit, padding=(4, 4))
        abar.pack(fill='x')
        ab1 = ttk.Button(abar, text='Run OSV Scan', command=self.run_osv)
        ab1.pack(side='left')
        ab2 = ttk.Button(abar, text='Force Rescan',
                         command=lambda: self.run_osv(force=True))
        ab2.pack(side='left', padx=(4, 0))
        self.audit_text = tk.Text(self.tab_audit, wrap='none', height=20)
        self.audit_text.pack(fill='both', expand=True, padx=4, pady=4)

        self._action_buttons.extend([gb1, gb2, gb3, pb1, pb2, pb3, ab1, ab2])

    # ── フィルタバー ──────────────────────────────────────────────────────────
    _STATUS_OPTIONS = [
        ('All', None),
        ('Outdated', 'outdated'),
        ('Major', 'major'),
        ('Minor', 'minor'),
        ('Both', 'both'),
        ('Latest', 'latest'),
        ('Unknown', 'unknown'),
    ]

    def _make_filter_bar(self, parent, table: PackageTable) -> None:
        bar = ttk.Frame(parent, padding=(4, 2))
        bar.pack(fill='x')

        ttk.Label(bar, text='Filter:').pack(side='left')
        search_var = tk.StringVar()
        ttk.Entry(bar, textvariable=search_var, width=24).pack(side='left', padx=(4, 0))

        ttk.Label(bar, text='Status:').pack(side='left', padx=(12, 4))
        status_var = tk.StringVar(value='All')
        status_combo = ttk.Combobox(
            bar, textvariable=status_var, state='readonly',
            values=[label for label, _ in self._STATUS_OPTIONS],
            width=9,
        )
        status_combo.pack(side='left')

        dev_var = tk.BooleanVar()
        ttk.Checkbutton(bar, text='Dev only', variable=dev_var).pack(side='left', padx=(12, 0))

        count_label = ttk.Label(bar, text='', foreground='#666')
        count_label.pack(side='right')

        def label_to_status(label: str) -> str | None:
            for lab, val in self._STATUS_OPTIONS:
                if lab == label:
                    return val
            return None

        def apply_filter(*_args):
            table.set_filter(
                query=search_var.get(),
                status=label_to_status(status_var.get()),
                dev_only=dev_var.get(),
            )

        search_var.trace_add('write', apply_filter)
        status_combo.bind('<<ComboboxSelected>>', apply_filter)
        dev_var.trace_add('write', apply_filter)

        table.on_render = lambda visible, total: count_label.config(text=f'{visible} / {total}')

    # ── 共通: ステータス / busy ────────────────────────────────────────────
    def _set_status(self, text: str, color: str = '#0a6') -> None:
        self.status_label.config(text=text, foreground=color)

    def _set_busy(self, busy: bool) -> None:
        if busy and not self._busy:
            self.progress.pack(side='right', padx=(0, 8))
            self.progress.start(80)
            for b in self._action_buttons:
                b.state(['disabled'])
            self._busy = True
        elif not busy and self._busy:
            self.progress.stop()
            self.progress.pack_forget()
            for b in self._action_buttons:
                b.state(['!disabled'])
            self._busy = False

    def _post_progress(self, done: int, total: int, label: str) -> None:
        self.after(0, lambda: self._set_status(f'{label}: {done}/{total}'))

    def _run_bg(self, work, on_done) -> None:
        self._set_busy(True)

        def runner():
            try:
                result = work()
                err = None
            except Exception as e:  # noqa: BLE001
                result, err = None, e

            def finish():
                self._set_busy(False)
                on_done(result, err)
            self.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()

    # ── Cooldown ─────────────────────────────────────────────────────────
    def _on_cooldown_changed(self) -> None:
        try:
            days = max(0, int(self.cooldown_var.get()))
        except (TypeError, ValueError):
            days = 7
        state.set_cooldown_days(days)
        self._set_status(f'Cooldown を {days} 日に設定 (Refresh で反映)')

    def _cooldown(self) -> int:
        try:
            return max(0, int(self.cooldown_var.get()))
        except (TypeError, ValueError):
            return state.get_cooldown_days()

    # ── プロジェクト選択 ─────────────────────────────────────────────────
    def choose_project(self) -> None:
        chosen = filedialog.askdirectory(
            title='Choose project folder (must contain pyproject.toml or requirements.txt)'
        )
        if not chosen:
            return
        self._open_project(Path(chosen))

    def _open_project(self, project_path: Path) -> None:
        self.current_project = project_path
        self._last_osv = None
        self._set_recent_and_select(str(project_path))
        self.notebook.select(self.tab_project)
        self.refresh_project()

    def _set_recent_and_select(self, path_str: str) -> None:
        items = state.add_recent_project(path_str, predicate=_is_py_project)
        self.project_combo['values'] = items
        self.project_combo.set(items[0] if items else path_str)

    def _on_recent_selected(self, _event) -> None:
        chosen = self.project_combo.get()
        if not chosen:
            return
        path = Path(chosen)
        if not path.is_dir():
            messagebox.showerror('PypkgUpdater', f'フォルダが存在しません:\n{chosen}')
            self.project_combo['values'] = state.remove_recent_project(
                chosen, predicate=_is_py_project,
            )
            self.project_combo.set('')
            return
        self._open_project(path)

    # ── Project / Global の refresh ──────────────────────────────────────
    def refresh_project(self, force: bool = False) -> None:
        if not self.current_project:
            self._set_status('Choose a project first', color='#a60')
            return
        if not (
            (self.current_project / 'pyproject.toml').exists()
            or (self.current_project / 'requirements.txt').exists()
        ):
            messagebox.showerror(
                'PypkgUpdater',
                f'pyproject.toml / requirements.txt not found in:\n{self.current_project}',
            )
            return

        cooldown = self._cooldown()
        cache_key = f'pypi_project_{self.current_project}_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_table(self.project_table, cached, from_cache=True)
                return

        deps = pyproject.collect_dependencies(self.current_project)
        self._set_status(
            f'Fetching from PyPI: 0/{len(deps)} (cooldown={cooldown}d)'
        )

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'Fetching from PyPI')
            infos = pypi.fetch_many(
                [(d['name'], d['version']) for d in deps],
                on_progress=on_prog,
                cooldown_days=cooldown,
            )
            return _build_package_list(deps, infos)

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            cache.save(cache_key, {'packages': result})
            self._render_table(self.project_table, {'packages': result}, from_cache=False)

        self._run_bg(work, done)

    def refresh_global(self, force: bool = False) -> None:
        cooldown = self._cooldown()
        cache_key = f'pypi_global_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_table(self.global_table, cached, from_cache=True)
                return

        self._set_status(f'Listing global packages (pip list)… (cooldown={cooldown}d)')

        def work():
            installed = pip_global.list_global_packages()
            if not installed:
                return {'packages': [], 'error': 'pip が見つからないか、パッケージがありません'}
            deps = [
                {'name': p['name'], 'version': p['version'], 'dev': False, 'group': None}
                for p in installed
            ]
            total = len(deps)
            self.after(0, lambda: self._set_status(f'Fetching from PyPI: 0/{total}'))

            def on_prog(done_count, t):
                self._post_progress(done_count, t, 'Fetching from PyPI')
            infos = pypi.fetch_many(
                [(d['name'], d['version']) for d in deps],
                on_progress=on_prog,
                cooldown_days=cooldown,
            )
            return {'packages': _build_package_list(deps, infos)}

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            cache.save(cache_key, result)
            self._render_table(self.global_table, result, from_cache=False)

        self._run_bg(work, done)

    def _render_table(self, table: PackageTable, payload: dict, from_cache: bool) -> None:
        if payload.get('error'):
            self._set_status(payload['error'], color='#a60')
        else:
            self._set_status('Loaded from cache' if from_cache else 'Updated')
        table.set_packages(payload.get('packages', []))

    # ── OSV ─────────────────────────────────────────────────────────────
    def run_osv(self, force: bool = False) -> None:
        if not self.current_project:
            messagebox.showinfo('PypkgUpdater', 'Choose a project first.')
            return
        self.audit_text.delete('1.0', 'end')

        # lock がない初期版なので直接依存のみスキャン (将来 uv.lock / poetry.lock 対応)
        pyproject_file = self.current_project / 'pyproject.toml'
        req_file = self.current_project / 'requirements.txt'
        mtime_src = pyproject_file if pyproject_file.exists() else req_file
        cache_key = f'pypi_osv_{self.current_project}'
        if not force:
            cached = cache.load(cache_key, _OSV_CACHE_TTL, invalidate_if_newer=mtime_src)
            if cached:
                self._last_osv = cached
                self._render_osv(
                    cached.get('results') or [],
                    cached.get('scanned') or [],
                    cached.get('source') or '(cached)',
                )
                self._set_status(
                    f'OSV (cache): {len(cached.get("results") or [])} vulnerable / '
                    f'{len(cached.get("scanned") or [])} scanned'
                )
                return

        deps = [
            {'name': d['name'], 'version': d['version'], 'direct': True, 'dev': d.get('dev', False)}
            for d in pyproject.collect_dependencies(self.current_project)
            if d['version']
        ]
        source = 'pyproject.toml / requirements.txt (lock 未対応: 直接依存のみ)'

        if not deps:
            self._set_status('OSV: バージョンが解決できる直接依存がありません', color='#a60')
            self.audit_text.insert(
                'end', 'バージョンが特定できる依存が見つかりませんでした。\n'
                'pyproject.toml の version-spec が範囲だけ (>=1.0 など) の場合は\n'
                'uv.lock / poetry.lock 対応を待つか、requirements.txt に固定版を書いてください。\n'
            )
            return

        self._set_status(f'Querying OSV.dev: {len(deps)} packages from {source}…')

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'OSV scan')
            results = osv.query_batch(
                [{'name': d['name'], 'version': d['version']} for d in deps],
                on_progress=on_prog,
                ecosystem='PyPI',
            )
            results.sort(key=lambda r: min(
                (osv.SEVERITY_ORDER.get(v['severity'], 99) for v in r['vulns']),
                default=99,
            ))
            return {'results': results, 'scanned': deps, 'source': source}

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            cache.save(cache_key, result)
            self._last_osv = result
            self._render_osv(result['results'] or [], result['scanned'], result['source'])
            self._set_status(
                f'OSV: {len(result["results"] or [])} vulnerable / {len(result["scanned"])} scanned'
            )

        self._run_bg(work, done)

    def _render_osv(self, results: list[dict], scanned: list[dict], source: str) -> None:
        direct = sum(1 for d in scanned if d.get('direct'))
        self.audit_text.insert('end', f'スキャン元: {source}\n')
        self.audit_text.insert('end', f'対象: 直接 {direct} 件 (計 {len(scanned)})\n\n')
        if not results:
            self.audit_text.insert('end', '脆弱性は検出されませんでした。\n')
            return
        for r in results:
            self.audit_text.insert('end', f'■ {r["name"]}@{r["version"]}\n')
            for v in r['vulns']:
                self.audit_text.insert('end', f'  - [{v["severity"]}] {v["id"]}: {v["summary"]}\n')
                self.audit_text.insert('end', f'    {v["url"]}\n')
            self.audit_text.insert('end', '\n')

    # ── PyPI ページを開く ───────────────────────────────────────────────
    def _open_selected_pypi(self, scope: str) -> None:
        table = self.project_table if scope == 'project' else self.global_table
        pkg = table.get_selected()
        if not pkg:
            messagebox.showinfo('PypkgUpdater', 'Select a package first.')
            return
        webbrowser.open(f'https://pypi.org/project/{pkg["name"]}/')


def _is_py_project(path: str) -> bool:
    """Python プロジェクトの判定: pyproject.toml か requirements.txt がルートにあれば対象。

    state.json は両 GUI で共有しているため、recent_projects ドロップダウンは
    自分の ecosystem のものだけに絞り込んでから表示する。
    """
    p = Path(path)
    return (p / 'pyproject.toml').is_file() or (p / 'requirements.txt').is_file()


def _build_package_list(deps: list[dict], infos: dict[str, dict]) -> list[dict]:
    """deps と PyPI info を結合して PackageTable に渡せる形に整形。"""
    out = []
    for d in deps:
        info = infos.get(d['name']) or {}
        latest = info.get('latest')
        latest_minor = info.get('latestMinor')
        latest_major = info.get('latestMajor')
        status = pep440.classify(d.get('version'), latest)
        if latest_minor and latest_major:
            status = 'both'
        out.append({
            'name': d['name'],
            'current': d.get('version'),
            'latest': latest,
            'latestMinor': latest_minor,
            'latestMajor': latest_major,
            'status': status,
            'dev': d.get('dev', False),
            'group': d.get('group'),
            'currentPublishedAt': info.get('currentPublishedAt'),
            'latestPublishedAt': info.get('latestPublishedAt'),
            'latestMinorPublishedAt': info.get('latestMinorPublishedAt'),
            'latestMajorPublishedAt': info.get('latestMajorPublishedAt'),
            'currentAgeInDays': info.get('currentAgeInDays'),
            'latestMinorAgeInDays': info.get('latestMinorAgeInDays'),
            'latestMajorAgeInDays': info.get('latestMajorAgeInDays'),
            'deprecated': info.get('deprecated'),
            'latestDeprecated': info.get('latestDeprecated'),
            'license': info.get('license'),
            'repositoryUrl': info.get('repositoryUrl'),
        })
    return out
