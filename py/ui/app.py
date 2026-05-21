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

from shared import cache, history, osv, state, ui_tabs, ui_tooltip
from shared.install_dialog import InstallDialog
from shared.safe_install_dialog import SafeInstallDialog

from py.core import (
    pep440, pip_global, pkg_manager, pypi, pyproject, requirements_writer,
)

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
        ttk.Label(top, text='プロジェクト:').pack(side='left')
        self.project_combo = ttk.Combobox(top, state='readonly', width=70)
        self.project_combo['values'] = state.load_recent_projects(predicate=_is_py_project)
        self.project_combo.pack(side='left', padx=(4, 8))
        self.project_combo.bind('<<ComboboxSelected>>', self._on_recent_selected)
        self.choose_btn = ttk.Button(top, text='フォルダ選択…', command=self.choose_project)
        ui_tooltip.attach(self.choose_btn, 'Choose…: pyproject.toml か requirements.txt があるフォルダを開く')
        self.choose_btn.pack(side='left')

        ttk.Label(top, text='  クールダウン:').pack(side='left', padx=(12, 2))
        self.cooldown_var = tk.IntVar(value=state.get_cooldown_days())
        self.cooldown_spin = ttk.Spinbox(
            top, from_=0, to=90, width=4,
            textvariable=self.cooldown_var,
            command=self._on_cooldown_changed,
        )
        self.cooldown_spin.pack(side='left')
        ttk.Label(top, text='日').pack(side='left', padx=(2, 0))
        ui_tooltip.attach(
            self.cooldown_spin,
            'Cooldown: 公開から N 日経っていない版を最新候補から除外 '
            '(供給チェーン攻撃対策のバッファ)。',
        )

        self.debug_log_btn = ttk.Button(top, text='デバッグログ…', command=self._open_debug_log)
        ui_tooltip.attach(self.debug_log_btn, 'Debug Log…: subprocess 失敗等の永続記録を閲覧')
        self.debug_log_btn.pack(side='left', padx=(12, 0))

        self._busy = False
        self._action_buttons: list[ttk.Button] = [self.choose_btn]

        # 画面下部のステータスバー (長いフェッチメッセージ用)。
        # bottom 系を先に pack してから notebook を pack することで
        # ウィンドウを縮めてもステータスバーが画面外に消えない。
        status_bar = ttk.Frame(self, padding=(8, 3))
        status_bar.pack(side='bottom', fill='x')
        status_bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_bar, text='', foreground='#0a6', anchor='w')
        self.status_label.grid(row=0, column=0, sticky='ew')
        self.progress = ttk.Progressbar(status_bar, mode='indeterminate', length=140)
        # progress は busy 中だけ grid。grid_remove で隠す。

        self.notebook = ttk.Notebook(self, style=ui_tabs.ensure_notebook_style())
        self.notebook.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))

        # Global tab (左端: argv なし時のデフォルト)
        self.tab_global = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_global, text='Global (pip list)')
        gbar = ttk.Frame(self.tab_global, padding=(4, 4))
        gbar.pack(fill='x')
        gb1 = ttk.Button(gbar, text='再取得', command=self.refresh_global)
        ui_tooltip.attach(gb1, 'Refresh: グローバル (現在の python) パッケージ一覧を再取得')
        gb1.pack(side='left')
        gb2 = ttk.Button(gbar, text='強制再取得',
                         command=lambda: self.refresh_global(force=True))
        ui_tooltip.attach(gb2, 'Force Refresh: cache を無視して再取得')
        gb2.pack(side='left', padx=(4, 0))
        gb_min = ttk.Button(gbar, text='Minor版に更新…',
                            command=lambda: self._install_selected('global', 'minor'))
        ui_tooltip.attach(gb_min, 'Install Minor Up: 同 major 内の最新版に更新 (spec 無視)')
        gb_min.pack(side='left', padx=(12, 0))
        gb_maj = ttk.Button(gbar, text='Major版に更新…',
                            command=lambda: self._install_selected('global', 'major'))
        ui_tooltip.attach(gb_maj, 'Install Major Up: 次 major へ更新 (Breaking Change の可能性)')
        gb_maj.pack(side='left', padx=(4, 0))
        gb_safe = ttk.Button(gbar, text='安全インストール…',
                             command=self._safe_install_global)
        ui_tooltip.attach(
            gb_safe,
            'Safe Install…: 未インストールパッケージを cooldown 適用後の版で個別に追加',
        )
        gb_safe.pack(side='left', padx=(4, 0))
        gb3 = ttk.Button(gbar, text='PyPIで開く',
                         command=lambda: self._open_selected_pypi('global'))
        ui_tooltip.attach(gb3, 'Open on PyPI: 選択行のパッケージページをブラウザで開く')
        gb3.pack(side='left', padx=(12, 0))
        self.global_table = PackageTable(self.tab_global)
        # Global は spec が無いため Wanted 列は常に空。既定は Latest にしておく。
        self._make_filter_bar(self.tab_global, self.global_table, default_preset='Latest')
        self.global_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Project tab
        self.tab_project = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_project, text='プロジェクト')
        pbar = ttk.Frame(self.tab_project, padding=(4, 4))
        pbar.pack(fill='x')
        pb1 = ttk.Button(pbar, text='再取得', command=self.refresh_project)
        ui_tooltip.attach(pb1, 'Refresh: 依存一覧を再取得 (cache 利用)')
        pb1.pack(side='left')
        pb2 = ttk.Button(pbar, text='強制再取得',
                         command=lambda: self.refresh_project(force=True))
        ui_tooltip.attach(pb2, 'Force Refresh: cache を無視して再取得')
        pb2.pack(side='left', padx=(4, 0))
        pb_wnt = ttk.Button(pbar, text='Wanted版で更新…',
                            command=lambda: self._install_selected('project', 'wanted'))
        ui_tooltip.attach(pb_wnt, 'Install Wanted: spec (== ~= >= 等) が許す最高版へ更新')
        pb_wnt.pack(side='left', padx=(12, 0))
        pb_min = ttk.Button(pbar, text='Minor版に更新…',
                            command=lambda: self._install_selected('project', 'minor'))
        ui_tooltip.attach(pb_min, 'Install Minor Up: 同 major 内の最新版に更新 (spec 無視)')
        pb_min.pack(side='left', padx=(4, 0))
        pb_maj = ttk.Button(pbar, text='Major版に更新…',
                            command=lambda: self._install_selected('project', 'major'))
        ui_tooltip.attach(pb_maj, 'Install Major Up: 次 major へ更新 (Breaking Change の可能性)')
        pb_maj.pack(side='left', padx=(4, 0))
        pb_safe = ttk.Button(pbar, text='安全インストール…',
                             command=self._safe_install_project)
        ui_tooltip.attach(
            pb_safe,
            'Safe Install…: 未導入のパッケージを cooldown 適用後の版で個別に追加 '
            '(uv add / poetry add / pip install -U)',
        )
        pb_safe.pack(side='left', padx=(4, 0))
        pb3 = ttk.Button(pbar, text='PyPIで開く',
                         command=lambda: self._open_selected_pypi('project'))
        ui_tooltip.attach(pb3, 'Open on PyPI: 選択行のパッケージページをブラウザで開く')
        pb3.pack(side='left', padx=(12, 0))
        self.project_table = PackageTable(self.tab_project)
        self._make_filter_bar(self.tab_project, self.project_table)
        self.project_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Audit tab (OSV PyPI ecosystem)
        self.tab_audit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_audit, text='監査 (OSV)')
        abar = ttk.Frame(self.tab_audit, padding=(4, 4))
        abar.pack(fill='x')
        ab1 = ttk.Button(abar, text='OSVスキャン実行', command=self.run_osv)
        ui_tooltip.attach(ab1, 'Run OSV Scan: OSV.dev (PyPI ecosystem) で脆弱性スキャン')
        ab1.pack(side='left')
        ab2 = ttk.Button(abar, text='強制再スキャン',
                         command=lambda: self.run_osv(force=True))
        ui_tooltip.attach(ab2, 'Force Rescan: cache を無視して OSV.dev に再問い合わせ')
        ab2.pack(side='left', padx=(4, 0))
        self.audit_text = tk.Text(self.tab_audit, wrap='none', height=20)
        self.audit_text.pack(fill='both', expand=True, padx=4, pady=4)

        self._action_buttons.extend([
            gb1, gb2, gb_min, gb_maj, gb_safe, gb3,
            pb1, pb2, pb_wnt, pb_min, pb_maj, pb_safe, pb3,
            ab1, ab2,
        ])

    # ── フィルタバー ──────────────────────────────────────────────────────────
    # (label, internal value) — label は UI 表示用、value は status と対応。
    _STATUS_OPTIONS = [
        ('全て',     None),
        ('古い',     'outdated'),
        ('Major',    'major'),
        ('Minor',    'minor'),
        ('両方',     'both'),
        ('最新',     'latest'),
        ('未導入',   'not_installed'),
        ('不明',     'unknown'),
    ]

    def _make_filter_bar(
        self, parent, table: PackageTable, default_preset: str = 'Wanted',
    ) -> None:
        from py.ui.table import VIEW_PRESETS, VIEW_PRESET_DESCRIPTIONS  # local import to avoid module-level coupling
        bar = ttk.Frame(parent, padding=(4, 2))
        bar.pack(fill='x')

        ttk.Label(bar, text='検索:').pack(side='left')
        search_var = tk.StringVar()
        ttk.Entry(bar, textvariable=search_var, width=24).pack(side='left', padx=(4, 0))

        ttk.Label(bar, text='状態:').pack(side='left', padx=(12, 4))
        status_var = tk.StringVar(value='全て')
        status_combo = ttk.Combobox(
            bar, textvariable=status_var, state='readonly',
            values=[label for label, _ in self._STATUS_OPTIONS],
            width=9,
        )
        status_combo.pack(side='left')

        dev_var = tk.BooleanVar()
        ttk.Checkbutton(bar, text='Devのみ', variable=dev_var).pack(side='left', padx=(12, 0))

        count_label = ttk.Label(bar, text='', foreground='#666')
        count_label.pack(side='right')

        # View プリセット radio: 列が多いので task 別に切替える。
        # 押すと treeview の displaycolumns だけ切り替わるので一瞬で反映される。
        # 各 radio には hover で説明ツールチップを出す (VIEW_PRESET_DESCRIPTIONS)。
        view_var = tk.StringVar(value=default_preset)
        for label in reversed(list(VIEW_PRESETS.keys())):
            # side='right' で並べると逆順で配置されるので reversed する → 結果として左から Wanted/Latest/Audit/All
            rb = ttk.Radiobutton(
                bar, text=label, value=label, variable=view_var,
                command=lambda l=label: table.set_view_preset(l),
            )
            rb.pack(side='right')
            desc = VIEW_PRESET_DESCRIPTIONS.get(label)
            if desc:
                ui_tooltip.attach(rb, desc)
        view_label = ttk.Label(bar, text='表示:', foreground='#666')
        view_label.pack(side='right', padx=(16, 4))
        ui_tooltip.attach(
            view_label,
            '表示列のプリセットを切替えます。データ再取得は不要で、列の見え方だけ変わります。',
        )
        table.set_view_preset(default_preset)

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
            self.progress.grid(row=0, column=1, padx=(8, 0), sticky='e')
            self.progress.start(80)
            for b in self._action_buttons:
                b.state(['disabled'])
            self._busy = True
        elif not busy and self._busy:
            self.progress.stop()
            self.progress.grid_remove()
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

    # ── Debug Log dialog ────────────────────────────────────────────────
    def _open_debug_log(self) -> None:
        from shared.debug_log_dialog import DebugLogDialog
        DebugLogDialog(self, app_name='PypkgUpdater')

    # ── Cooldown ─────────────────────────────────────────────────────────
    def _on_cooldown_changed(self) -> None:
        try:
            days = max(0, int(self.cooldown_var.get()))
        except (TypeError, ValueError):
            days = 7
        state.set_cooldown_days(days)
        self._set_status(f'Cooldown を {days} 日に設定 (再取得で反映)')

    def _cooldown(self) -> int:
        try:
            return max(0, int(self.cooldown_var.get()))
        except (TypeError, ValueError):
            return state.get_cooldown_days()

    # ── プロジェクト選択 ─────────────────────────────────────────────────
    def choose_project(self) -> None:
        chosen = filedialog.askdirectory(
            title='プロジェクトフォルダを選択 (pyproject.toml か requirements.txt が必要)'
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
            self._set_status('先にプロジェクトを選択してください', color='#a60')
            return
        if not (
            (self.current_project / 'pyproject.toml').exists()
            or (self.current_project / 'requirements.txt').exists()
        ):
            messagebox.showerror(
                'PypkgUpdater',
                f'pyproject.toml / requirements.txt が見つかりません:\n{self.current_project}',
            )
            return

        # プロジェクト切替時の「旧データが残ったまま」を避けるため一旦テーブルを空にする。
        # cache hit のときは同 turn 内で set_packages するので画面上は瞬時に新データに置き換わる
        # (tkinter は callback 終了まで再描画しない)。fetch のときは空のまま fetch 完了を待つ形。
        self.project_table.set_packages([])

        cooldown = self._cooldown()
        cache_key = f'pypi_project_{self.current_project}_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_table(self.project_table, cached, from_cache=True)
                return

        deps = pyproject.collect_dependencies(self.current_project)
        # .venv の site-packages を覗いて実インストール版を埋める。
        pyproject.attach_installed_info(self.current_project, deps)
        not_installed_n = sum(1 for d in deps if not d.get('installed'))
        ni_label = f', 未導入 {not_installed_n}' if not_installed_n else ''
        self._set_status(
            f'PyPI から取得: 0/{len(deps)} (cooldown={cooldown}日{ni_label})'
        )

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'PyPI から取得')
            infos = pypi.fetch_many(
                # registry 問い合わせの current は「実インストール版 → spec 正規化版」の順で
                # フォールバック。currentPublishedAt 等は実版で参照したいので installed_version を優先。
                [(d['name'], d.get('installed_version') or d.get('version'), d.get('spec'))
                 for d in deps],
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
        # 切替直後に旧データが残らないよう一旦空にする (refresh_project と同じ理由)。
        self.global_table.set_packages([])

        cooldown = self._cooldown()
        cache_key = f'pypi_global_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_table(self.global_table, cached, from_cache=True)
                return

        self._set_status(f'グローバルパッケージを列挙中 (pip list)… (cooldown={cooldown}日)')

        def work():
            installed = pip_global.list_global_packages()
            if not installed:
                return {'packages': [], 'error': 'pip が見つからないか、パッケージがありません'}
            # Global は `pip list` の結果なので常に installed=True。
            deps = [
                {
                    'name': p['name'], 'version': p['version'],
                    'installed_version': p['version'], 'installed': True,
                    'dev': False, 'group': None,
                }
                for p in installed
            ]
            total = len(deps)
            self.after(0, lambda: self._set_status(f'PyPI から取得: 0/{total}'))

            def on_prog(done_count, t):
                self._post_progress(done_count, t, 'PyPI から取得')
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
            # error / 空応答はキャッシュしない (PATH 問題などで一時的に取れなかった時に
            # その失敗が TTL 中ずっと張り付くのを避ける)。
            if not result.get('error') and result.get('packages'):
                cache.save(cache_key, result)
            self._render_table(self.global_table, result, from_cache=False)

        self._run_bg(work, done)

    def _render_table(self, table: PackageTable, payload: dict, from_cache: bool) -> None:
        if payload.get('error'):
            self._set_status(payload['error'], color='#a60')
        else:
            self._set_status('cache から読込' if from_cache else '再取得完了')
        table.set_packages(payload.get('packages', []))

    # ── OSV ─────────────────────────────────────────────────────────────
    def run_osv(self, force: bool = False) -> None:
        if not self.current_project:
            messagebox.showinfo('PypkgUpdater', '先にプロジェクトを選択してください')
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

        self._set_status(f'OSV.dev へ問い合わせ: {len(deps)} 件 / source={source}…')

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'OSV スキャン')
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
            messagebox.showinfo('PypkgUpdater', '先にパッケージを選択してください')
            return
        webbrowser.open(f'https://pypi.org/project/{pkg["name"]}/')

    # ── Safe Install (単一パッケージ ・cooldown 適用) ──────────────────
    def _safe_install_global(self) -> None:
        """未インストールパッケージを cooldown 適用後の版で 1 つずつ安全に追加。

        Global は常に pip install -U で実行 (PypkgUpdater の Global タブは
        現在の python が見ている site-packages を相手にするため)。
        """
        SafeInstallDialog(
            self,
            title='Safe Install (Global / pip install -U)',
            pm='pip',
            global_install=True,
            cwd=None,
            cooldown_days=self._cooldown(),
            resolver=pypi.resolve_for_install,
            pkg_manager=pkg_manager,
            opener=pip_global.open_command_prompt,
            on_installed=lambda: self.refresh_global(force=True),
        )

    def _safe_install_project(self) -> None:
        """プロジェクトに未導入のパッケージを cooldown 適用後の版で追加。

        PM は uv.lock / poetry.lock / Pipfile から検出 (uv add / poetry add / pip install)。
        uv add / poetry add は pyproject.toml も書き換えるため、再取得で
        Wanted/Latest が変わる可能性がある。
        """
        if not self.current_project:
            messagebox.showinfo('PypkgUpdater', '先にプロジェクトを選択してください')
            return
        pm = pkg_manager.detect(self.current_project)
        SafeInstallDialog(
            self,
            title=f'Safe Install (Project / {pm} add)',
            pm=pm,
            global_install=False,
            cwd=str(self.current_project),
            cooldown_days=self._cooldown(),
            resolver=pypi.resolve_for_install,
            pkg_manager=pkg_manager,
            opener=pip_global.open_command_prompt,
            on_installed=lambda: self.refresh_project(force=True),
        )

    # ── Install (uv add / poetry add / pip install -U) ──────────────────
    def _install_selected(self, scope: str, target: str) -> None:
        """scope: 'project' | 'global'   target: 'minor' | 'major'

        複数選択時は 1 つの install コマンドにまとめて起動する。
        対象更新が無いパッケージはスキップして確認ダイアログで通知。
        """
        table = self.project_table if scope == 'project' else self.global_table
        selected = table.get_selected_all()
        if not selected:
            messagebox.showinfo('PypkgUpdater', '先に 1 つ以上のパッケージを選択してください')
            return
        if scope == 'project' and not self.current_project:
            messagebox.showinfo('PypkgUpdater', '先にプロジェクトを選択してください')
            return

        # target:
        #   'wanted' = requirements の spec が許す最高版 (Wanted 列の値)
        #   'minor'  = 同 major 内の最高 (spec 無視)
        #   'major'  = より上の major (spec 無視)
        # 'wanted' では以下もスキップ:
        #   - current と同じ (= 既に上限)
        #   - '?' (= spec 解釈不能なので install 対象を決められない)
        key_map = {'wanted': 'allowedLatest', 'minor': 'latestMinor', 'major': 'latestMajor'}
        key = key_map.get(target, 'latestMinor')
        targets, skipped = [], []
        for p in selected:
            v = p.get(key)
            if target == 'wanted':
                if v and v != '?' and v != p.get('current'):
                    targets.append((p['name'], v))
                else:
                    skipped.append(p['name'])
            else:
                if v:
                    targets.append((p['name'], v))
                else:
                    skipped.append(p['name'])

        if not targets:
            label_map = {
                'wanted': 'Wanted (within spec)',
                'minor': 'Minor (same major)',
                'major': 'Major up',
            }
            label = label_map.get(target, target)
            messagebox.showinfo(
                'PypkgUpdater',
                f'選択された {len(selected)} 件すべてに {label} の更新候補はありません。',
            )
            return

        is_global = (scope == 'global')
        cwd = None if is_global else str(self.current_project)
        # PyPI spec: `name==version`。pip / uv / poetry / pipenv で共通記法。
        specs = [f'{n}=={v}' for n, v in targets]
        pm = 'pip' if is_global else pkg_manager.detect(self.current_project)
        target_label = {
            'wanted': 'Install Wanted (within spec)',
            'minor': 'Install Minor Up',
            'major': 'Install Major Up',
        }.get(target, target)

        from_versions = {p['name']: p.get('current') for p in selected}

        dialog = InstallDialog(
            self,
            title_label=target_label,
            specs=specs,
            skipped=skipped,
            cwd=cwd,
            global_install=is_global,
            pm=pm,
            pkg_manager=pkg_manager,
        )
        self.wait_window(dialog)
        if dialog.result != 'install':
            return

        # pip プロジェクトでは pyproject.toml / lock の自動書き換えが無いので、
        # requirements*.txt の operator スタイルを保ったまま version を同期する。
        # uv / poetry は uv add / poetry add 側がファイルを書き換えるため skip。
        req_summary = ''
        if pm == 'pip' and not is_global and self.current_project:
            update_map = {name: ver for name, ver in targets}
            rewrite_results = requirements_writer.rewrite_in_project(
                self.current_project, update_map,
            )
            if rewrite_results:
                req_summary = ' | requirements: ' + requirements_writer.summarize(rewrite_results)

        cmd = pkg_manager.install_command(pm, specs, global_install=is_global)
        try:
            pip_global.open_command_prompt(cmd, cwd=cwd)
            self._set_status(
                f'別 console で実行中 [{pm}]: {len(specs)} 件を install '
                f'({"global" if is_global else "project"})' + req_summary
            )
            # 履歴記録 (プロジェクトスコープのみ。global は記録先プロジェクトが無いため除外)
            if not is_global and self.current_project:
                history.append(
                    project_path=str(self.current_project),
                    pm=pm,
                    scope=scope,
                    specs=specs,
                    from_versions=from_versions,
                )
        except OSError as e:
            messagebox.showerror('PypkgUpdater', f'プロンプトの起動に失敗しました\n\n{e}')


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
        installed_version = d.get('installed_version')
        is_installed = d.get('installed')
        if is_installed is None:
            is_installed = installed_version is not None
        current_display = installed_version if is_installed else None
        if not is_installed:
            status = 'not_installed'
        else:
            status = pep440.classify(installed_version or d.get('version'), latest)
            if latest_minor and latest_major:
                status = 'both'
        # 未導入時は「現在版に紐づく」field をクリアする。spec 正規化版
        # ('==13.7.1' → '13.7.1') を current_version として fetch_one に渡しているため、
        # そのままだと「入っていない版の公開日 / yanked 状態」が Current 列の付随情報
        # として表示されてしまう。
        current_pub = info.get('currentPublishedAt') if is_installed else None
        current_age = info.get('currentAgeInDays') if is_installed else None
        deprecated = info.get('deprecated') if is_installed else None
        out.append({
            'name': d['name'],
            'current': current_display,
            'installed': is_installed,
            'spec': d.get('spec'),
            'latest': latest,
            'latestMinor': latest_minor,
            'latestMajor': latest_major,
            'allowedLatest': info.get('allowedLatest'),
            'status': status,
            'dev': d.get('dev', False),
            'group': d.get('group'),
            'currentPublishedAt': current_pub,
            'latestPublishedAt': info.get('latestPublishedAt'),
            'latestMinorPublishedAt': info.get('latestMinorPublishedAt'),
            'latestMajorPublishedAt': info.get('latestMajorPublishedAt'),
            'allowedLatestPublishedAt': info.get('allowedLatestPublishedAt'),
            'currentAgeInDays': current_age,
            'latestMinorAgeInDays': info.get('latestMinorAgeInDays'),
            'latestMajorAgeInDays': info.get('latestMajorAgeInDays'),
            'allowedLatestAgeInDays': info.get('allowedLatestAgeInDays'),
            'deprecated': deprecated,
            'latestDeprecated': info.get('latestDeprecated'),
            'license': info.get('license'),
            'repositoryUrl': info.get('repositoryUrl'),
        })
    return out
