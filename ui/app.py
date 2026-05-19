"""NodeUpdater メインウィンドウ。"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import cache, npm_global, npm_registry, osv, package_json, package_lock, semver, state

from .table import PackageTable

_CACHE_TTL = 24 * 60 * 60  # 24h (registry メタデータ)
_OSV_CACHE_TTL = 12 * 60 * 60  # 12h (脆弱性 DB は registry より変化が早い想定)


class App(tk.Tk):
    def __init__(self, initial_project: Path | None = None):
        super().__init__()
        self.title('NodeUpdater')
        self.geometry('1100x640')
        self.minsize(820, 480)

        self._build_layout()

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
        # 履歴付きドロップダウン: 過去に開いたプロジェクトをすぐ選び直せる
        self.project_combo = ttk.Combobox(top, state='readonly', width=70)
        self.project_combo['values'] = state.load_recent_projects()
        self.project_combo.pack(side='left', padx=(4, 8))
        self.project_combo.bind('<<ComboboxSelected>>', self._on_recent_selected)
        self.choose_btn = ttk.Button(top, text='Choose…', command=self.choose_project)
        self.choose_btn.pack(side='left')

        # 供給チェーンバッファ: 公開から N 日経っていない版を最新候補から除外する
        ttk.Label(top, text='  Cooldown:').pack(side='left', padx=(12, 2))
        self.cooldown_var = tk.IntVar(value=state.get_cooldown_days())
        self.cooldown_spin = ttk.Spinbox(
            top, from_=0, to=90, width=4,
            textvariable=self.cooldown_var,
            command=self._on_cooldown_changed,
        )
        self.cooldown_spin.pack(side='left')
        ttk.Label(top, text='日').pack(side='left', padx=(2, 0))

        # 右側: 進捗バー + ステータスラベル
        self.status_label = ttk.Label(top, text='', foreground='#0a6')
        self.status_label.pack(side='right')
        self.progress = ttk.Progressbar(top, mode='indeterminate', length=140)
        # pack はローディング中だけ。pack_forget で隠す
        self._busy = False
        # 操作ボタンの参照（busy 中は disable）
        self._action_buttons: list[ttk.Button] = [self.choose_btn]

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        # Global tab (左端: argv なし起動時のデフォルト)
        self.tab_global = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_global, text='Global (npm -g)')
        global_bar = ttk.Frame(self.tab_global, padding=(4, 4))
        global_bar.pack(fill='x')
        b4 = ttk.Button(global_bar, text='Refresh', command=self.refresh_global)
        b4.pack(side='left')
        b5 = ttk.Button(global_bar, text='Force Refresh', command=lambda: self.refresh_global(force=True))
        b5.pack(side='left', padx=(4, 0))
        b6 = ttk.Button(global_bar, text='Install Minor Up…',
                        command=lambda: self._install_selected('global', 'minor'))
        b6.pack(side='left', padx=(12, 0))
        b6b = ttk.Button(global_bar, text='Install Major Up…',
                         command=lambda: self._install_selected('global', 'major'))
        b6b.pack(side='left', padx=(4, 0))
        b6c = ttk.Button(global_bar, text='Open on npm',
                         command=lambda: self._open_selected_npm('global'))
        b6c.pack(side='left', padx=(12, 0))
        self.global_table = PackageTable(self.tab_global, on_select=self._on_row_select)
        self._make_filter_bar(self.tab_global, self.global_table, key='global')
        self.global_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Project tab (Project と Audit は対で隣接)
        self.tab_project = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_project, text='Project')
        project_bar = ttk.Frame(self.tab_project, padding=(4, 4))
        project_bar.pack(fill='x')
        b1 = ttk.Button(project_bar, text='Refresh', command=self.refresh_project)
        b1.pack(side='left')
        b2 = ttk.Button(project_bar, text='Force Refresh', command=lambda: self.refresh_project(force=True))
        b2.pack(side='left', padx=(4, 0))
        b3a = ttk.Button(project_bar, text='Install Minor Up…',
                         command=lambda: self._install_selected('project', 'minor'))
        b3a.pack(side='left', padx=(12, 0))
        b3b = ttk.Button(project_bar, text='Install Major Up…',
                         command=lambda: self._install_selected('project', 'major'))
        b3b.pack(side='left', padx=(4, 0))
        b3 = ttk.Button(project_bar, text='Open on npm',
                        command=lambda: self._open_selected_npm('project'))
        b3.pack(side='left', padx=(12, 0))
        self.project_table = PackageTable(self.tab_project, on_select=self._on_row_select)
        self._make_filter_bar(self.tab_project, self.project_table, key='project')
        self.project_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Audit tab (Project と対)
        self.tab_audit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_audit, text='Audit (OSV)')
        audit_bar = ttk.Frame(self.tab_audit, padding=(4, 4))
        audit_bar.pack(fill='x')
        b7 = ttk.Button(audit_bar, text='Run OSV Scan', command=self.run_osv)
        b7.pack(side='left')
        b7b = ttk.Button(audit_bar, text='Force Rescan',
                         command=lambda: self.run_osv(force=True))
        b7b.pack(side='left', padx=(4, 0))
        b8 = ttk.Button(audit_bar, text='Run npm audit', command=self.run_npm_audit)
        b8.pack(side='left', padx=(12, 0))
        b9 = ttk.Button(audit_bar, text='npm audit fix…',
                        command=lambda: self._run_audit_fix(force=False))
        b9.pack(side='left', padx=(4, 0))
        b10 = ttk.Button(audit_bar, text='npm audit fix --force…',
                         command=lambda: self._run_audit_fix(force=True))
        b10.pack(side='left', padx=(4, 0))
        self.audit_text = tk.Text(self.tab_audit, wrap='none', height=20)
        self.audit_text.pack(fill='both', expand=True, padx=4, pady=4)

        self._action_buttons.extend(
            [b1, b2, b3, b3a, b3b, b4, b5, b6, b6b, b6c, b7, b7b, b8, b9, b10]
        )

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

    def _make_filter_bar(self, parent, table: PackageTable, key: str) -> None:
        """Project / Global タブに名前検索・状態・dev フィルタの行を追加。"""
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

        # データ更新やフィルタ変更で再描画されるたびに件数を反映
        table.on_render = lambda visible, total: count_label.config(text=f'{visible} / {total}')

    # ── 選択時の補助表示 ──────────────────────────────────────────────────────
    def _on_row_select(self, pkg: dict) -> None:
        """deprecated パッケージ選択時に message を status に表示。"""
        msg = pkg.get('deprecated') or pkg.get('latestDeprecated')
        if msg:
            tag = 'current' if pkg.get('deprecated') else 'latest'
            # 1 行に収まる長さに丸める
            short = msg if len(msg) <= 120 else msg[:117] + '…'
            self._set_status(f'[deprecated/{tag}] {short}', color='#c60')

    # ── Cooldown 設定 ─────────────────────────────────────────────────────────
    def _on_cooldown_changed(self) -> None:
        """Spinbox 操作で永続化。実反映は次回 Refresh から。"""
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

    # ── ステータス表示 ─────────────────────────────────────────────────────────
    def _set_status(self, text: str, color: str = '#0a6') -> None:
        self.status_label.config(text=text, foreground=color)

    def _set_busy(self, busy: bool) -> None:
        """進捗バー開始/停止と操作ボタンの有効/無効切替。"""
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
        """別スレッドから安全に進捗を反映するためのヘルパ。"""
        self.after(0, lambda: self._set_status(f'{label}: {done}/{total}'))

    def _run_bg(self, work, on_done) -> None:
        """work() をスレッド実行し、結果を main thread の on_done(result, error) に渡す。"""
        self._set_busy(True)
        def runner():
            try:
                result = work()
                err = None
            except Exception as e:
                result, err = None, e
            def finish():
                self._set_busy(False)
                on_done(result, err)
            self.after(0, finish)
        threading.Thread(target=runner, daemon=True).start()

    # ── 操作 ─────────────────────────────────────────────────────────────────
    def choose_project(self) -> None:
        chosen = filedialog.askdirectory(title='Choose project folder (must contain package.json)')
        if not chosen:
            return
        self._open_project(Path(chosen))

    def _open_project(self, project_path: Path) -> None:
        """新しいプロジェクトを選択した時の共通処理: 履歴更新 + 切り替え + refresh。"""
        self.current_project = project_path
        self._set_recent_and_select(str(project_path))
        self.notebook.select(self.tab_project)
        self.refresh_project()

    def _set_recent_and_select(self, path_str: str) -> None:
        """履歴に追加してドロップダウンを再構築、先頭を選択状態にする。"""
        items = state.add_recent_project(path_str)
        self.project_combo['values'] = items
        # 正規化済みの先頭値を反映
        self.project_combo.set(items[0] if items else path_str)

    def _on_recent_selected(self, _event) -> None:
        chosen = self.project_combo.get()
        if not chosen:
            return
        path = Path(chosen)
        if not path.is_dir():
            messagebox.showerror('NodeUpdater', f'フォルダが存在しません:\n{chosen}')
            # 履歴から消す
            self.project_combo['values'] = state.remove_recent_project(chosen)
            self.project_combo.set('')
            return
        # 同じプロジェクトを再選択した場合もリフレッシュは走らせる（ユーザー意図優先）
        self._open_project(path)

    def refresh_project(self, force: bool = False) -> None:
        if not self.current_project:
            self._set_status('Choose a project first', color='#a60')
            return
        if not (self.current_project / 'package.json').exists():
            messagebox.showerror('NodeUpdater', f'package.json not found in:\n{self.current_project}')
            return

        cooldown = self._cooldown()
        cache_key = f'project_{self.current_project}_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_project(cached, from_cache=True)
                return

        deps = package_json.collect_dependencies(self.current_project)
        self._set_status(f'Fetching from npm registry: 0/{len(deps)} (cooldown={cooldown}d)')

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'Fetching from npm registry')
            infos = npm_registry.fetch_many(
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
            self._render_project({'packages': result}, from_cache=False)

        self._run_bg(work, done)

    def _render_project(self, payload: dict, from_cache: bool) -> None:
        self.project_table.set_packages(payload.get('packages', []))
        self._set_status('Loaded from cache' if from_cache else 'Updated')

    def refresh_global(self, force: bool = False) -> None:
        cooldown = self._cooldown()
        cache_key = f'global_npm_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_global(cached, from_cache=True)
                return

        self._set_status(f'Listing global packages (npm list -g)… (cooldown={cooldown}d)')

        def work():
            installed = npm_global.list_global_packages()
            if not installed:
                return {'packages': [], 'error': 'npm が見つからないか、グローバルパッケージがありません'}
            deps = [{'name': p['name'], 'version': p['version'], 'dev': False} for p in installed]
            total = len(deps)
            self.after(0, lambda: self._set_status(f'Fetching from npm registry: 0/{total}'))
            def on_prog(done_count, t):
                self._post_progress(done_count, t, 'Fetching from npm registry')
            infos = npm_registry.fetch_many(
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
            self._render_global(result, from_cache=False)

        self._run_bg(work, done)

    def _render_global(self, payload: dict, from_cache: bool) -> None:
        if payload.get('error'):
            self._set_status(payload['error'], color='#a60')
        else:
            self._set_status('Loaded from cache' if from_cache else 'Updated')
        self.global_table.set_packages(payload.get('packages', []))

    def run_osv(self, force: bool = False) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        self.audit_text.delete('1.0', 'end')

        # キャッシュ: TTL 内かつ lock/package.json が更新されていなければ流用。
        lock_file = self.current_project / 'package-lock.json'
        pkg_file = self.current_project / 'package.json'
        mtime_src = lock_file if lock_file.exists() else pkg_file
        cache_key = f'osv_{self.current_project}'
        if not force:
            cached = cache.load(cache_key, _OSV_CACHE_TTL, invalidate_if_newer=mtime_src)
            if cached:
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

        # package-lock.json があれば推移依存も含めて全件スキャン。
        # 無ければ package.json の直接依存のみ (旧挙動)。
        lock_deps = package_lock.read(self.current_project)
        if lock_deps:
            deps = lock_deps
            source = 'package-lock.json'
        else:
            deps = [
                {'name': d['name'], 'version': d['version'], 'direct': True, 'dev': d.get('dev', False)}
                for d in package_json.collect_dependencies(self.current_project)
                if d['version']
            ]
            source = 'package.json (lock 無し: 直接依存のみ)'

        direct_n = sum(1 for d in deps if d.get('direct'))
        self._set_status(f'Querying OSV.dev: {len(deps)} packages ({direct_n} direct) from {source}…')

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'OSV scan')
            results = osv.query_batch(
                [{'name': d['name'], 'version': d['version']} for d in deps],
                on_progress=on_prog,
            )
            # パッケージは含まれる最も深刻な vuln の severity 順に並べる
            results.sort(key=lambda r: min(
                (osv.SEVERITY_ORDER.get(v['severity'], 99) for v in r['vulns']), default=99
            ))
            return {'results': results, 'scanned': deps, 'source': source}

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            cache.save(cache_key, result)
            self._render_osv(result['results'] or [], result['scanned'], result['source'])
            self._set_status(
                f'OSV: {len(result["results"] or [])} vulnerable / {len(result["scanned"])} scanned'
            )

        self._run_bg(work, done)

    def _render_osv(self, results: list[dict], scanned: list[dict], source: str) -> None:
        direct = sum(1 for d in scanned if d.get('direct'))
        transitive = len(scanned) - direct
        self.audit_text.insert('end', f'スキャン元: {source}\n')
        self.audit_text.insert(
            'end', f'対象: 直接 {direct} 件 / 推移 {transitive} 件 (計 {len(scanned)})\n\n'
        )

        if not results:
            self.audit_text.insert('end', '脆弱性は検出されませんでした。\n')
            return

        info_by_nv = {(d['name'], d['version']): d for d in scanned}
        for r in results:
            info = info_by_nv.get((r['name'], r['version']), {})
            if info.get('direct'):
                kind = '直接'
            else:
                roots = info.get('roots') or []
                if roots:
                    shown = ', '.join(roots[:5])
                    if len(roots) > 5:
                        shown += f' ほか{len(roots) - 5}件'
                    kind = f'推移 ← {shown}'
                else:
                    kind = '推移'
            dev_tag = ' [dev]' if info.get('dev') else ''
            self.audit_text.insert('end', f'■ {r["name"]}@{r["version"]} ({kind}{dev_tag})\n')
            for v in r['vulns']:
                self.audit_text.insert('end', f'  - [{v["severity"]}] {v["id"]}: {v["summary"]}\n')
                self.audit_text.insert('end', f'    {v["url"]}\n')
            self.audit_text.insert('end', '\n')

    # ── npm audit / audit fix ────────────────────────────────────────────────
    def run_npm_audit(self) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        if not (self.current_project / 'package.json').exists():
            messagebox.showerror('NodeUpdater', 'package.json not found.')
            return
        self.audit_text.delete('1.0', 'end')
        self._set_status('Running `npm audit --json`…')
        project_str = str(self.current_project)

        def work():
            return npm_global.run_npm_audit(project_str)

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            if not result:
                self._set_status('npm audit failed', color='#c00')
                self.audit_text.insert(
                    'end', 'npm audit を実行できませんでした (npm 未インストール / タイムアウト)。\n'
                )
                return
            self._render_npm_audit(result)
            meta = (result.get('metadata') or {}).get('vulnerabilities') or {}
            total = meta.get('total') or 0
            self._set_status(f'npm audit: {total} vulnerabilities')

        self._run_bg(work, done)

    def _render_npm_audit(self, data: dict) -> None:
        if data.get('error'):
            err = data['error']
            self.audit_text.insert('end', f'npm audit エラー: {err.get("code", "?")}\n')
            self.audit_text.insert('end', f'{err.get("summary", "")}\n')
            if err.get('detail'):
                self.audit_text.insert('end', f'\n{err["detail"]}\n')
            return

        meta = (data.get('metadata') or {}).get('vulnerabilities') or {}
        self.audit_text.insert('end', 'npm audit 結果\n')
        self.audit_text.insert(
            'end',
            '注: npm audit は npm 独自 advisory DB を参照するため OSV と件数が異なる場合があります。\n\n',
        )
        self.audit_text.insert(
            'end',
            f'サマリ: critical={meta.get("critical", 0)} high={meta.get("high", 0)} '
            f'moderate={meta.get("moderate", 0)} low={meta.get("low", 0)} '
            f'info={meta.get("info", 0)} (total {meta.get("total", 0)})\n\n',
        )

        vulns = data.get('vulnerabilities') or {}
        if not vulns:
            self.audit_text.insert('end', '脆弱性は検出されませんでした。\n')
            return

        sev_order = {'critical': 0, 'high': 1, 'moderate': 2, 'low': 3, 'info': 4}
        items = sorted(vulns.items(), key=lambda kv: sev_order.get(kv[1].get('severity', ''), 99))
        for name, info in items:
            sev = str(info.get('severity', 'unknown')).upper()
            rng = info.get('range', '') or ''
            direct = '直接' if info.get('isDirect') else '推移'
            fix = info.get('fixAvailable')
            if isinstance(fix, dict):
                major = ' (major)' if fix.get('isSemVerMajor') else ''
                fix_text = f'fix: {fix.get("name", "?")}@{fix.get("version", "?")}{major}'
            elif fix is True:
                fix_text = 'fix: 可'
            else:
                fix_text = 'fix: 不可'
            self.audit_text.insert('end', f'■ {name} {rng} [{sev}] ({direct}) [{fix_text}]\n')

    def _run_audit_fix(self, force: bool) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        cmd = 'npm audit fix --force' if force else 'npm audit fix'
        if force:
            msg = (
                f'新しいコマンドプロンプトで以下を実行します:\n\n  {cmd}\n\n'
                f'実行場所: {self.current_project}\n\n'
                f'!! --force は major バージョン更新を含む Breaking Change を\n'
                f'   適用する可能性があります。完了後は必ず diff と動作を確認してください。\n\n'
                f'続行しますか?'
            )
        else:
            msg = (
                f'新しいコマンドプロンプトで以下を実行します:\n\n  {cmd}\n\n'
                f'実行場所: {self.current_project}\n\n'
                f'続行しますか?'
            )
        if not messagebox.askyesno('NodeUpdater', msg):
            return
        try:
            npm_global.open_command_prompt(cmd, cwd=str(self.current_project))
            self._set_status(f'Opened prompt: {cmd}  (Refresh after completion)')
        except OSError as e:
            messagebox.showerror('NodeUpdater', f'プロンプトの起動に失敗しました\n\n{e}')

    # ── 選択行に対する操作 ────────────────────────────────────────────────────
    def _selected_pkg(self, scope: str) -> dict | None:
        table = self.project_table if scope == 'project' else self.global_table
        return table.get_selected()

    def _open_selected_npm(self, scope: str) -> None:
        pkg = self._selected_pkg(scope)
        if not pkg:
            messagebox.showinfo('NodeUpdater', 'Select a package first.')
            return
        webbrowser.open(f'https://www.npmjs.com/package/{pkg["name"]}')

    def _install_selected(self, scope: str, target: str) -> None:
        """scope: 'project' | 'global'   target: 'minor' | 'major'"""
        pkg = self._selected_pkg(scope)
        if not pkg:
            messagebox.showinfo('NodeUpdater', 'Select a package first.')
            return
        if scope == 'project' and not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return

        version = pkg.get('latestMinor') if target == 'minor' else pkg.get('latestMajor')
        if not version:
            label = 'Minor (same major)' if target == 'minor' else 'Major up'
            messagebox.showinfo('NodeUpdater', f'{pkg["name"]} に {label} の更新候補はありません。')
            return

        is_global = (scope == 'global')
        cwd = None if is_global else str(self.current_project)
        g = '-g ' if is_global else ''
        cmd = f'npm install {g}{pkg["name"]}@{version}'
        location = '(global)' if is_global else f'(in {self.current_project})'

        if not messagebox.askyesno(
            'NodeUpdater',
            f'新しいコマンドプロンプトで以下を実行します:\n\n  {cmd}\n\n'
            f'実行場所: {location}\n'
            f'完了後はプロンプトに結果が残るので確認できます。\n'
            f'更新後の状態を反映するには Refresh を押してください。\n\n'
            f'続行しますか？'
        ):
            return
        try:
            npm_global.open_install_prompt(
                pkg['name'], version=version, cwd=cwd, global_install=is_global,
            )
            self._set_status(f'Opened prompt: {cmd}  (Refresh after completion)')
        except OSError as e:
            messagebox.showerror('NodeUpdater', f'プロンプトの起動に失敗しました\n\n{e}')


def _build_package_list(deps: list[dict], infos: dict[str, dict]) -> list[dict]:
    """deps と registry info を結合して PackageTable に渡せる形に整形。"""
    out = []
    for d in deps:
        info = infos.get(d['name']) or {}
        latest = info.get('latest')
        latest_minor = info.get('latestMinor')
        latest_major = info.get('latestMajor')
        status = semver.classify(d.get('version'), latest)
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
            'currentPublishedAt': info.get('currentPublishedAt'),
            'latestPublishedAt': info.get('latestPublishedAt'),
            'latestMinorPublishedAt': info.get('latestMinorPublishedAt'),
            'latestMajorPublishedAt': info.get('latestMajorPublishedAt'),
            'currentAgeInDays': info.get('currentAgeInDays'),
            'latestMinorAgeInDays': info.get('latestMinorAgeInDays'),
            'latestMajorAgeInDays': info.get('latestMajorAgeInDays'),
            'provenance': info.get('provenance'),
            'deprecated': info.get('deprecated'),
            'latestDeprecated': info.get('latestDeprecated'),
            'license': info.get('license'),
        })
    return out
