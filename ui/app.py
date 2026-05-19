"""NodeUpdater メインウィンドウ。"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import cache, npm_global, npm_registry, osv, package_json, package_lock, semver, state

from .table import PackageTable

_CACHE_TTL = 24 * 60 * 60  # 24h


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
        self.global_table = PackageTable(self.tab_global)
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
        self.project_table = PackageTable(self.tab_project)
        self.project_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Audit tab (Project と対)
        self.tab_audit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_audit, text='Audit (OSV)')
        audit_bar = ttk.Frame(self.tab_audit, padding=(4, 4))
        audit_bar.pack(fill='x')
        b7 = ttk.Button(audit_bar, text='Run OSV Scan', command=self.run_osv)
        b7.pack(side='left')
        self.audit_text = tk.Text(self.tab_audit, wrap='none', height=20)
        self.audit_text.pack(fill='both', expand=True, padx=4, pady=4)

        self._action_buttons.extend([b1, b2, b3, b3a, b3b, b4, b5, b6, b6b, b6c, b7])

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

        cache_key = f'project_{self.current_project}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_project(cached, from_cache=True)
                return

        deps = package_json.collect_dependencies(self.current_project)
        self._set_status(f'Fetching from npm registry: 0/{len(deps)}')

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'Fetching from npm registry')
            infos = npm_registry.fetch_many(
                [(d['name'], d['version']) for d in deps],
                on_progress=on_prog,
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
        cache_key = 'global_npm'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_global(cached, from_cache=True)
                return

        self._set_status('Listing global packages (npm list -g)…')

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

    def run_osv(self) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        self.audit_text.delete('1.0', 'end')

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
            results = osv.query_batch([{'name': d['name'], 'version': d['version']} for d in deps])
            # パッケージは含まれる最も深刻な vuln の severity 順に並べる
            results.sort(key=lambda r: min(
                (osv.SEVERITY_ORDER.get(v['severity'], 99) for v in r['vulns']), default=99
            ))
            return {'results': results, 'scanned': deps, 'source': source}

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
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
            tag = '直接' if info.get('direct') else '推移'
            dev_tag = ' [dev]' if info.get('dev') else ''
            self.audit_text.insert('end', f'■ {r["name"]}@{r["version"]} ({tag}{dev_tag})\n')
            for v in r['vulns']:
                self.audit_text.insert('end', f'  - [{v["severity"]}] {v["id"]}: {v["summary"]}\n')
                self.audit_text.insert('end', f'    {v["url"]}\n')
            self.audit_text.insert('end', '\n')

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
        })
    return out
