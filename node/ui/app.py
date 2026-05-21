"""NodeUpdater メインウィンドウ。"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from shared import (
    audit_export,
    cache,
    history,
    osv,
    state,
    ui_tabs,
    ui_tooltip,
)
from node.core import (
    bun_lock,
    bundlephobia,
    npm_global,
    npm_registry,
    package_json,
    package_lock,
    pkg_manager,
    semver,
)

from shared.install_dialog import InstallDialog
from shared.safe_install_dialog import SafeInstallDialog

from .changelog_dialog import ChangelogDialog
from .history_dialog import HistoryDialog
from .settings_dialog import SettingsDialog
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

        # エクスポート用に直近の OSV / npm audit 結果を保持
        self._last_osv: dict | None = None
        self._last_npm_audit: dict | None = None

        # 現プロジェクトのワークスペース一覧と選択中の path ('' がルート)
        self._workspaces: list[dict] = []
        self._current_workspace: str = ''

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
        # 履歴付きドロップダウン: 過去に開いたプロジェクトをすぐ選び直せる
        self.project_combo = ttk.Combobox(top, state='readonly', width=70)
        self.project_combo['values'] = state.load_recent_projects(predicate=_is_node_project)
        self.project_combo.pack(side='left', padx=(4, 8))
        self.project_combo.bind('<<ComboboxSelected>>', self._on_recent_selected)
        self.choose_btn = ttk.Button(top, text='フォルダ選択…', command=self.choose_project)
        ui_tooltip.attach(self.choose_btn, 'Choose…: package.json があるフォルダを開く')
        self.choose_btn.pack(side='left')

        # 供給チェーンバッファ: 公開から N 日経っていない版を最新候補から除外する
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

        self.history_btn = ttk.Button(top, text='履歴…', command=self._open_history)
        ui_tooltip.attach(self.history_btn, 'History…: このプロジェクトの過去 install 履歴')
        self.history_btn.pack(side='left', padx=(12, 0))

        self.settings_btn = ttk.Button(top, text='設定…', command=self._open_settings)
        ui_tooltip.attach(self.settings_btn, 'Settings…: proxy / 並列数 / registry URL 等')
        self.settings_btn.pack(side='left', padx=(4, 0))

        self.debug_log_btn = ttk.Button(top, text='デバッグログ…', command=self._open_debug_log)
        ui_tooltip.attach(self.debug_log_btn, 'Debug Log…: subprocess 失敗等の永続記録を閲覧')
        self.debug_log_btn.pack(side='left', padx=(4, 0))

        self._busy = False
        # 操作ボタンの参照（busy 中は disable）
        self._action_buttons: list[ttk.Button] = [self.choose_btn]

        # 画面下部のステータスバー (フェッチ進捗・操作結果を表示)。
        # 長いメッセージで top bar が押し出されないよう独立配置。
        # pack 順: bottom 系を先に pack してから notebook を pack することで
        # ウィンドウを縮めた時にステータスバーが画面外に消えないようにする
        # (CLAUDE.md の Toplevel フッター規約と同じ理由)。
        # 内側は grid: label と progress の左右配置を pack_forget/再 pack で
        # 順序が崩れないようにするため。
        status_bar = ttk.Frame(self, padding=(8, 3))
        status_bar.pack(side='bottom', fill='x')
        status_bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_bar, text='', foreground='#0a6', anchor='w')
        self.status_label.grid(row=0, column=0, sticky='ew')
        self.progress = ttk.Progressbar(status_bar, mode='indeterminate', length=140)
        # progress は busy 中だけ grid。grid_remove で隠す。

        self.notebook = ttk.Notebook(self, style=ui_tabs.ensure_notebook_style())
        self.notebook.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))

        # Global tab (左端: argv なし起動時のデフォルト)
        self.tab_global = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_global, text='Global (npm -g)')
        global_bar = ttk.Frame(self.tab_global, padding=(4, 4))
        global_bar.pack(fill='x')
        b4 = ttk.Button(global_bar, text='リロード', command=self.refresh_global)
        ui_tooltip.attach(b4, 'Refresh: グローバルパッケージ一覧をリロード (cache 利用)')
        b4.pack(side='left')
        b5 = ttk.Button(global_bar, text='強制リロード', command=lambda: self.refresh_global(force=True))
        ui_tooltip.attach(b5, 'Force Refresh: cache を無視してリロード')
        b5.pack(side='left', padx=(4, 0))
        self._btn_glob_minor = ttk.Button(global_bar, text='Minor版に更新…',
                        command=lambda: self._install_selected('global', 'minor'))
        ui_tooltip.attach(self._btn_glob_minor, 'Install Minor Up…: 選択行を同 major 内の最新版に更新')
        self._btn_glob_minor.pack(side='left', padx=(12, 0))
        self._btn_glob_major = ttk.Button(global_bar, text='Major版に更新…',
                         command=lambda: self._install_selected('global', 'major'))
        ui_tooltip.attach(self._btn_glob_major, 'Install Major Up…: 選択行を次 major へ更新 (Breaking Change の可能性)')
        self._btn_glob_major.pack(side='left', padx=(4, 0))
        b6e = ttk.Button(global_bar, text='安全インストール…',
                         command=self._safe_install_global)
        ui_tooltip.attach(
            b6e,
            'Safe Install…: 未インストールパッケージを cooldown 適用後の版で個別に追加',
        )
        b6e.pack(side='left', padx=(4, 0))
        b6c = ttk.Button(global_bar, text='npmで開く',
                         command=lambda: self._open_selected_npm('global'))
        ui_tooltip.attach(b6c, 'Open on npm: 選択行のパッケージページをブラウザで開く')
        b6c.pack(side='left', padx=(12, 0))
        b6d = ttk.Button(global_bar, text='変更履歴…',
                         command=lambda: self._show_changelog('global'))
        ui_tooltip.attach(b6d, 'Changelog…: GitHub Releases から変更履歴を取得')
        b6d.pack(side='left', padx=(4, 0))
        # Global は spec が無いため Wanted 列は意味を持たない。Wanted preset と
        # 'All' preset 内の wanted/age_wnt 列は VIEW_PRESETS_GLOBAL で除外している。
        from node.ui.table import VIEW_PRESETS_GLOBAL
        self.global_table = PackageTable(
            self.tab_global, on_select=self._on_row_select, presets=VIEW_PRESETS_GLOBAL,
        )
        self._make_filter_bar(
            self.tab_global, self.global_table, key='global', default_preset='Latest',
        )
        self.global_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Project tab (Project と Audit は対で隣接)
        self.tab_project = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_project, text='プロジェクト')
        project_bar = ttk.Frame(self.tab_project, padding=(4, 4))
        project_bar.pack(fill='x')
        b1 = ttk.Button(project_bar, text='リロード', command=self.refresh_project)
        ui_tooltip.attach(b1, 'Refresh: 依存一覧をリロード (cache 利用)')
        b1.pack(side='left')
        b2 = ttk.Button(project_bar, text='強制リロード', command=lambda: self.refresh_project(force=True))
        ui_tooltip.attach(b2, 'Force Refresh: cache を無視してリロード')
        b2.pack(side='left', padx=(4, 0))
        self._btn_proj_wanted = ttk.Button(project_bar, text='Wanted版で更新…',
                         command=lambda: self._install_selected('project', 'wanted'))
        ui_tooltip.attach(self._btn_proj_wanted, 'Install Wanted: spec (^/~/range) が許す最高版へ更新')
        self._btn_proj_wanted.pack(side='left', padx=(12, 0))
        self._btn_proj_minor = ttk.Button(project_bar, text='Minor版に更新…',
                         command=lambda: self._install_selected('project', 'minor'))
        ui_tooltip.attach(self._btn_proj_minor, 'Install Minor Up: 同 major 内の最新版に更新 (spec 無視)')
        self._btn_proj_minor.pack(side='left', padx=(4, 0))
        self._btn_proj_major = ttk.Button(project_bar, text='Major版に更新…',
                         command=lambda: self._install_selected('project', 'major'))
        ui_tooltip.attach(self._btn_proj_major, 'Install Major Up: 次 major へ更新 (Breaking Change の可能性)')
        self._btn_proj_major.pack(side='left', padx=(4, 0))
        b3 = ttk.Button(project_bar, text='npmで開く',
                        command=lambda: self._open_selected_npm('project'))
        ui_tooltip.attach(b3, 'Open on npm: 選択行のパッケージページをブラウザで開く')
        b3.pack(side='left', padx=(12, 0))
        b3c = ttk.Button(project_bar, text='変更履歴…',
                         command=lambda: self._show_changelog('project'))
        ui_tooltip.attach(b3c, 'Changelog…: GitHub Releases から変更履歴を取得')
        b3c.pack(side='left', padx=(4, 0))

        # 右側: ワークスペースセレクタ (モノレポ時のみ表示)
        self.workspace_var = tk.StringVar()
        self.workspace_combo = ttk.Combobox(
            project_bar, textvariable=self.workspace_var, state='readonly', width=32,
        )
        self.workspace_combo.pack(side='right', padx=(0, 4))
        self.workspace_label = ttk.Label(project_bar, text='ワークスペース:')
        self.workspace_label.pack(side='right', padx=(12, 4))
        self.workspace_combo.bind('<<ComboboxSelected>>', self._on_workspace_changed)
        # 初期状態は非表示 (プロジェクト読み込み時に必要なら表示)
        self.workspace_combo.pack_forget()
        self.workspace_label.pack_forget()

        self.project_table = PackageTable(self.tab_project, on_select=self._on_row_select)
        self._make_filter_bar(self.tab_project, self.project_table, key='project')
        self.project_table.pack(fill='both', expand=True, padx=4, pady=4)

        # Tree tab (Project と対: 物理 node_modules 階層を表示)
        self.tab_tree = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_tree, text='依存ツリー')
        tree_bar = ttk.Frame(self.tab_tree, padding=(4, 4))
        tree_bar.pack(fill='x')
        b_tree_refresh = ttk.Button(tree_bar, text='リロード', command=self.refresh_tree)
        ui_tooltip.attach(b_tree_refresh, 'Refresh: package-lock.json から依存ツリーを再構築')
        b_tree_refresh.pack(side='left')
        b_tree_expand = ttk.Button(tree_bar, text='全展開', command=self._tree_expand_all)
        ui_tooltip.attach(b_tree_expand, 'Expand all: すべてのノードを開く')
        b_tree_expand.pack(side='left', padx=(8, 0))
        b_tree_collapse = ttk.Button(tree_bar, text='全折りたたみ', command=self._tree_collapse_all)
        ui_tooltip.attach(b_tree_collapse, 'Collapse all: すべてのノードを閉じる')
        b_tree_collapse.pack(side='left', padx=(4, 0))
        self.tree_count_label = ttk.Label(tree_bar, text='', foreground='#666')
        self.tree_count_label.pack(side='right')

        tree_frame = ttk.Frame(self.tab_tree)
        tree_frame.pack(fill='both', expand=True, padx=4, pady=4)
        self.dep_tree = ttk.Treeview(
            tree_frame,
            columns=('version', 'flags'),
            show='tree headings',
            selectmode='browse',
        )
        self.dep_tree.heading('#0', text='Package')
        self.dep_tree.heading('version', text='Version')
        self.dep_tree.heading('flags', text='Flags')
        self.dep_tree.column('#0', width=400, stretch=True)
        self.dep_tree.column('version', width=100, anchor='w')
        self.dep_tree.column('flags', width=120, anchor='w')
        # 脆弱性のあるノードを強調
        self.dep_tree.tag_configure('vuln', background='#ffd0d0')
        self.dep_tree.tag_configure('dev', foreground='#888')
        tree_vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.dep_tree.yview)
        self.dep_tree.configure(yscrollcommand=tree_vsb.set)
        self.dep_tree.grid(row=0, column=0, sticky='nsew')
        tree_vsb.grid(row=0, column=1, sticky='ns')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Audit tab (Project と対)
        self.tab_audit = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_audit, text='監査 (OSV)')
        audit_bar = ttk.Frame(self.tab_audit, padding=(4, 4))
        audit_bar.pack(fill='x')
        b7 = ttk.Button(audit_bar, text='OSVスキャン実行', command=self.run_osv)
        ui_tooltip.attach(b7, 'Run OSV Scan: OSV.dev で脆弱性スキャン (推移依存も含む)')
        b7.pack(side='left')
        b7b = ttk.Button(audit_bar, text='強制再スキャン',
                         command=lambda: self.run_osv(force=True))
        ui_tooltip.attach(b7b, 'Force Rescan: cache を無視して OSV.dev に再問い合わせ')
        b7b.pack(side='left', padx=(4, 0))
        b8 = ttk.Button(audit_bar, text='npm audit 実行', command=self.run_npm_audit)
        ui_tooltip.attach(b8, 'Run npm audit: npm 独自 advisory DB で脆弱性スキャン')
        b8.pack(side='left', padx=(12, 0))
        b9 = ttk.Button(audit_bar, text='npm audit fix…',
                        command=lambda: self._run_audit_fix(force=False))
        ui_tooltip.attach(b9, 'npm audit fix: 脆弱性を自動修正 (spec 内の更新のみ)')
        b9.pack(side='left', padx=(4, 0))
        b10 = ttk.Button(audit_bar, text='npm audit fix --force…',
                         command=lambda: self._run_audit_fix(force=True))
        ui_tooltip.attach(b10, '--force: Breaking Change を伴う major 更新も適用 (要確認)')
        b10.pack(side='left', padx=(4, 0))

        # エクスポート用の 2 段目
        export_bar = ttk.Frame(self.tab_audit, padding=(4, 0))
        export_bar.pack(fill='x')
        b_exp_osv = ttk.Button(export_bar, text='OSV結果を書き出し…', command=self._export_osv)
        ui_tooltip.attach(b_exp_osv, 'Export OSV…: 直近の OSV スキャン結果を md/csv に書き出し')
        b_exp_osv.pack(side='left')
        b_exp_npm = ttk.Button(export_bar, text='npm audit結果を書き出し…', command=self._export_npm_audit)
        ui_tooltip.attach(b_exp_npm, 'Export npm audit…: 直近の npm audit 結果を md/csv に書き出し')
        b_exp_npm.pack(side='left', padx=(8, 0))

        self.audit_text = tk.Text(self.tab_audit, wrap='none', height=20)
        self.audit_text.pack(fill='both', expand=True, padx=4, pady=4)

        self._action_buttons.extend(
            [b1, b2, b3, self._btn_proj_wanted, self._btn_proj_minor, self._btn_proj_major, b3c,
             b4, b5, self._btn_glob_minor, self._btn_glob_major, b6c, b6d, b6e,
             b7, b7b, b8, b9, b10,
             b_exp_osv, b_exp_npm,
             b_tree_refresh, b_tree_expand, b_tree_collapse]
        )
        # 選択依存ボタン: 起動直後は非選択状態なので全て disable で開始
        self._install_buttons = (
            self._btn_proj_wanted, self._btn_proj_minor, self._btn_proj_major,
            self._btn_glob_minor, self._btn_glob_major,
        )
        for btn in self._install_buttons:
            btn.state(['disabled'])

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
        self, parent, table: PackageTable, key: str,
        default_preset: str = 'Wanted',
    ) -> None:
        """Project / Global タブに名前検索・状態・dev フィルタの行を追加。

        default_preset で View プリセットの初期値を指定 (Global は spec が無いので Latest 推奨)。
        """
        from node.ui.table import VIEW_PRESET_DESCRIPTIONS  # local import
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

        # View プリセット radio (列が多いので task 別に切替える)
        # table が自分の presets を持つので、それを基に radio を生成する
        # (Global は Wanted preset が無いので 3 個になる)。
        view_var = tk.StringVar(value=default_preset)
        for label in reversed(list(table.presets.keys())):
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
            '表示列のプリセットを切替えます。データリロードは不要で、列の見え方だけ変わります。',
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

        # データ更新やフィルタ変更で再描画されるたびに件数を反映
        table.on_render = lambda visible, total: count_label.config(text=f'{visible} / {total}')

    # ── 選択時の補助表示 ──────────────────────────────────────────────────────
    def _on_row_select(self, selected: list[dict]) -> None:
        """選択変化時のフック: install ボタンの状態更新 + deprecated メッセージ表示。

        selected は常に list で渡る (Esc 等で選択 0 件になっても発火する)。
        """
        self._refresh_install_buttons()
        # deprecated メッセージは単一選択時のみ
        if len(selected) != 1:
            return
        pkg = selected[0]
        msg = pkg.get('deprecated') or pkg.get('latestDeprecated')
        if msg:
            tag = 'current' if pkg.get('deprecated') else 'latest'
            # 1 行に収まる長さに丸める
            short = msg if len(msg) <= 120 else msg[:117] + '…'
            self._set_status(f'[deprecated/{tag}] {short}', color='#c60')

    # ── 設定ダイアログ ────────────────────────────────────────────────────────
    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _open_debug_log(self) -> None:
        from shared.debug_log_dialog import DebugLogDialog
        DebugLogDialog(self, app_name='NodeUpdater')

    def _open_history(self) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        HistoryDialog(self, str(self.current_project))

    # ── Cooldown 設定 ─────────────────────────────────────────────────────────
    def _on_cooldown_changed(self) -> None:
        """Spinbox 操作で永続化。実反映は次回 Refresh から。"""
        try:
            days = max(0, int(self.cooldown_var.get()))
        except (TypeError, ValueError):
            days = 7
        state.set_cooldown_days(days)
        self._set_status(f'Cooldown を {days} 日に設定 (リロードで反映)')

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
            # busy で一律 enable に戻ったあと、選択状態に応じて install ボタンを再評価する
            self._refresh_install_buttons()

    # ── Install ボタン状態 (選択依存) ──────────────────────────────────────
    def _refresh_install_buttons(self) -> None:
        """両タブの選択状態を見て各 install ボタンの enable/disable を更新する。

        busy 中は触らない (`_set_busy` が一律 disable しており、解除時に再評価される)。
        """
        if self._busy:
            return
        self._update_install_state('project', self.project_table.get_selected_all())
        self._update_install_state('global', self.global_table.get_selected_all())

    def _update_install_state(self, scope: str, selected: list[dict]) -> None:
        """scope 側の install ボタンを、selected の各行が当該 target に
        有効な更新候補を持つかで切替える (1 行でも持てば enable)。"""
        if scope == 'project':
            specs = [
                (self._btn_proj_wanted, 'allowedLatest', True),
                (self._btn_proj_minor, 'latestMinor', False),
                (self._btn_proj_major, 'latestMajor', False),
            ]
        else:
            specs = [
                (self._btn_glob_minor, 'latestMinor', False),
                (self._btn_glob_major, 'latestMajor', False),
            ]
        for btn, key, is_wanted in specs:
            has_any = False
            for p in selected:
                v = p.get(key)
                if is_wanted:
                    # Wanted: '?' (解釈不能) や current と同じ場合は除外
                    if v and v != '?' and v != p.get('current'):
                        has_any = True
                        break
                else:
                    if v:
                        has_any = True
                        break
            btn.state(['!disabled'] if has_any else ['disabled'])

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
        # 別プロジェクトの古いスキャン結果でエクスポートしないようリセット
        self._last_osv = None
        self._last_npm_audit = None
        self._update_workspace_selector(project_path)
        self._set_recent_and_select(str(project_path))
        self.notebook.select(self.tab_project)
        self.refresh_project()

    def _update_workspace_selector(self, project_path: Path) -> None:
        """プロジェクトを開いた時にワークスペース一覧を再構築。"""
        self._workspaces = package_json.list_workspaces(project_path)
        self._current_workspace = self._workspaces[0]['path'] if self._workspaces else ''
        labels = [w['label'] for w in self._workspaces]
        self.workspace_combo['values'] = labels
        if labels:
            self.workspace_combo.set(labels[0])
        # モノレポ (>1) の時だけセレクタを表示
        if len(self._workspaces) > 1:
            self.workspace_label.pack(side='right', padx=(12, 4))
            self.workspace_combo.pack(side='right', padx=(0, 4))
        else:
            self.workspace_label.pack_forget()
            self.workspace_combo.pack_forget()

    def _on_workspace_changed(self, _event=None) -> None:
        selected = self.workspace_combo.get()
        for w in self._workspaces:
            if w['label'] == selected:
                if w['path'] != self._current_workspace:
                    self._current_workspace = w['path']
                    self.refresh_project()
                return

    def _set_recent_and_select(self, path_str: str) -> None:
        """履歴に追加してドロップダウンを再構築、先頭を選択状態にする。"""
        items = state.add_recent_project(path_str, predicate=_is_node_project)
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
            self.project_combo['values'] = state.remove_recent_project(
                chosen, predicate=_is_node_project,
            )
            self.project_combo.set('')
            return
        # 同じプロジェクトを再選択した場合もリフレッシュは走らせる（ユーザー意図優先）
        self._open_project(path)

    def refresh_project(self, force: bool = False) -> None:
        if not self.current_project:
            self._set_status('先にプロジェクトを選択してください', color='#a60')
            return
        if not (self.current_project / 'package.json').exists():
            messagebox.showerror('NodeUpdater', f'package.json not found in:\n{self.current_project}')
            return

        # プロジェクト切替時の「旧データが残ったまま」を避けるため一旦テーブルを空にする。
        # cache hit のときは同 turn 内で set_packages するので画面上は瞬時に新データに置き換わる
        # (tkinter は callback 終了まで再描画しない)。fetch のときは空のまま fetch 完了を待つ形になる。
        self.project_table.set_packages([])

        cooldown = self._cooldown()
        ws = self._current_workspace
        ws_key = f'_ws_{ws.replace("/", "_")}' if ws else ''
        cache_key = f'project_{self.current_project}_cd{cooldown}{ws_key}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_project(cached, from_cache=True)
                return

        deps = package_json.collect_dependencies_at(self.current_project, ws)
        # node_modules を覗いて実インストール版を埋める。workspaces を使うリポでも
        # node_modules はルートにホイストされるため project root を渡す。
        package_json.attach_installed_info(self.current_project, deps)
        ws_label = f' ws={ws or "."}' if len(self._workspaces) > 1 else ''
        not_installed_n = sum(1 for d in deps if not d.get('installed'))
        ni_label = f', 未導入 {not_installed_n}' if not_installed_n else ''
        self._set_status(
            f'npm registry へ問い合わせ: 0/{len(deps)} '
            f'(cooldown={cooldown}日{ws_label}{ni_label})'
        )

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'npm registry から取得')
            infos = npm_registry.fetch_many(
                # registry 問い合わせの current は「実インストール版があればそれ、
                # 無ければ spec 正規化版」をフォールバック。currentPublishedAt 等は
                # 実インストール版で参照したいので installed_version を優先する。
                [(d['name'], d.get('installed_version') or d.get('version'), d.get('spec'))
                 for d in deps],
                on_progress=on_prog,
                cooldown_days=cooldown,
            )
            pkg_list = _build_package_list(deps, infos)

            # bundlephobia から bundle size を後付け (version 単位で永続キャッシュ)
            def on_size_prog(done_count, total):
                if total:
                    self._post_progress(done_count, total, 'bundle size を取得')
            sizes = bundlephobia.fetch_many_cached(
                [(p['name'], p.get('current')) for p in pkg_list],
                on_progress=on_size_prog,
            )
            for p in pkg_list:
                s = sizes.get(p['name'])
                if s:
                    p['size'] = s.get('size')
                    p['gzip'] = s.get('gzip')
            return pkg_list

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            cache.save(cache_key, {'packages': result})
            self._render_project({'packages': result}, from_cache=False)

        self._run_bg(work, done)

    def _render_project(self, payload: dict, from_cache: bool) -> None:
        self.project_table.set_packages(payload.get('packages', []))
        self._set_status('cache から読込' if from_cache else 'リロード完了')

    def refresh_global(self, force: bool = False) -> None:
        # 切替直後に旧データが残らないよう一旦空にする (refresh_project と同じ理由)。
        self.global_table.set_packages([])

        cooldown = self._cooldown()
        cache_key = f'global_npm_cd{cooldown}'
        if not force:
            cached = cache.load(cache_key, _CACHE_TTL)
            if cached:
                self._render_global(cached, from_cache=True)
                return

        self._set_status(f'グローバルパッケージを列挙中 (npm list -g)… (cooldown={cooldown}日)')

        def work():
            installed = npm_global.list_global_packages()
            if not installed:
                # 失敗理由を last_error から拾って具体的なメッセージにする
                err = npm_global.last_error or {}
                reason = (err.get('reason') or '').lower()
                if 'timed out' in reason or 'timeout' in reason:
                    msg = (
                        f'`npm list -g` がタイムアウトしました ({err.get("reason")})。'
                        f'再度リロードしてみてください。続く場合は Debug Log… で詳細確認。'
                    )
                elif 'not found' in reason or 'filenotfound' in reason:
                    msg = 'npm が PATH に見つかりません。Debug Log… を確認してください。'
                elif reason:
                    msg = f'npm list -g 失敗: {err.get("reason")} (Debug Log… で詳細)'
                else:
                    msg = 'npm が見つからないか、グローバルパッケージがありません'
                return {'packages': [], 'error': msg}
            # Global は `npm list -g` の結果なので常に installed=True。
            deps = [{
                'name': p['name'], 'version': p['version'],
                'installed_version': p['version'], 'installed': True,
                'dev': False,
            } for p in installed]
            total = len(deps)
            self.after(0, lambda: self._set_status(f'npm registry から取得: 0/{total}'))
            def on_prog(done_count, t):
                self._post_progress(done_count, t, 'npm registry から取得')
            infos = npm_registry.fetch_many(
                [(d['name'], d['version'], d.get('spec')) for d in deps],
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
            self._render_global(result, from_cache=False)

        self._run_bg(work, done)

    def _render_global(self, payload: dict, from_cache: bool) -> None:
        if payload.get('error'):
            self._set_status(payload['error'], color='#a60')
        else:
            self._set_status('cache から読込' if from_cache else 'リロード完了')
        self.global_table.set_packages(payload.get('packages', []))

    def run_osv(self, force: bool = False) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        self.audit_text.delete('1.0', 'end')

        # キャッシュ: TTL 内かつ lock/package.json が更新されていなければ流用。
        # bun.lock を優先 (Bun プロジェクト)、無ければ package-lock.json、それも
        # 無ければ package.json をソースとする。mtime ベース失効判定の対象も同じ順。
        bun_file = self.current_project / 'bun.lock'
        npm_lock_file = self.current_project / 'package-lock.json'
        pkg_file = self.current_project / 'package.json'
        if bun_file.exists():
            mtime_src = bun_file
            source_tag = 'bun'
        elif npm_lock_file.exists():
            mtime_src = npm_lock_file
            source_tag = 'npm'
        else:
            mtime_src = pkg_file
            source_tag = 'pkg'
        # ソース種別をキーに含める: ロック種類が後から増えても古いキャッシュと衝突しない
        cache_key = f'osv_{self.current_project}_{source_tag}'
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

        # ロックファイルがあれば推移依存も含めて全件スキャン。
        # bun.lock → package-lock.json → package.json (直接依存のみ) の優先順位。
        if bun_file.exists():
            deps = bun_lock.read(self.current_project)
            source = 'bun.lock'
        elif npm_lock_file.exists():
            deps = package_lock.read(self.current_project)
            source = 'package-lock.json'
        else:
            deps = []
            source = ''

        if not deps:
            deps = [
                {'name': d['name'], 'version': d['version'], 'direct': True, 'dev': d.get('dev', False)}
                for d in package_json.collect_dependencies(self.current_project)
                if d['version']
            ]
            source = 'package.json (lock 無し: 直接依存のみ)'

        direct_n = sum(1 for d in deps if d.get('direct'))
        self._set_status(f'OSV.dev へ問い合わせ: {len(deps)} 件 (直接 {direct_n} 件) / source={source}…')

        def work():
            def on_prog(done_count, total):
                self._post_progress(done_count, total, 'OSV スキャン')
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
            self._last_osv = result
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

    # ── エクスポート ─────────────────────────────────────────────────────────
    def _export_save(self, data, kind: str, to_text) -> None:
        """共通: 保存ダイアログ → 拡張子で md/csv 自動判別 → 書き出し。"""
        if not data:
            messagebox.showinfo(
                'NodeUpdater', f'{kind} の結果がまだありません。先に実行してください。'
            )
            return
        project_name = self.current_project.name if self.current_project else 'report'
        default = f'{kind}_{project_name}.md'
        path = filedialog.asksaveasfilename(
            defaultextension='.md',
            initialfile=default,
            filetypes=[('Markdown', '*.md'), ('CSV', '*.csv'), ('All files', '*.*')],
        )
        if not path:
            return
        fmt = 'csv' if path.lower().endswith('.csv') else 'markdown'
        try:
            text = to_text(data, fmt=fmt, project_path=str(self.current_project or ''))
            Path(path).write_text(text, encoding='utf-8')
            self._set_status(f'{kind} レポートを書き出し: {path}')
        except OSError as e:
            messagebox.showerror('NodeUpdater', f'書き込みエラー\n\n{e}')

    def _export_osv(self) -> None:
        self._export_save(self._last_osv, 'osv', audit_export.osv_to_text)

    def _export_npm_audit(self) -> None:
        self._export_save(self._last_npm_audit, 'npm_audit', audit_export.npm_audit_to_text)

    # ── Tree タブ ─────────────────────────────────────────────────────────────
    def refresh_tree(self) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        self._render_tree([], 0, message='Loading…')
        project = self.current_project

        def work():
            data = package_lock.build_tree(project)
            # OSV キャッシュがあれば脆弱性のあるノードを強調する材料に
            vulns_map: dict[tuple[str, str], int] = {}
            lock_file = project / 'package-lock.json'
            mtime_src = lock_file if lock_file.exists() else (project / 'package.json')
            osv_cache = cache.load(f'osv_{project}', _OSV_CACHE_TTL, invalidate_if_newer=mtime_src)
            if osv_cache:
                for r in (osv_cache.get('results') or []):
                    vulns_map[(r.get('name'), r.get('version'))] = len(r.get('vulns') or [])
            return data, vulns_map

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                self._render_tree([], 0, message=str(err))
                return
            data, vulns_map = result
            if not data:
                if (project / 'bun.lock').exists():
                    msg = 'bun.lock のツリー表示は未対応です (OSV スキャンは利用可能)'
                else:
                    msg = 'package-lock.json が見つかりません'
                self._render_tree([], 0, message=msg)
                self._set_status('Tree: 表示不可', color='#a60')
                return
            self._render_tree(data['roots'], data['count'], vulns_map=vulns_map)
            self._set_status(
                f'Tree: {data["count"]} nodes'
                + (f' ({sum(vulns_map.values())} vulns highlighted)' if vulns_map else '')
            )

        self._run_bg(work, done)

    def _render_tree(
        self,
        roots: list[dict],
        count: int,
        vulns_map: dict | None = None,
        message: str | None = None,
    ) -> None:
        self.dep_tree.delete(*self.dep_tree.get_children())
        vulns_map = vulns_map or {}
        if message and not roots:
            self.dep_tree.insert('', 'end', text=message, values=('', ''))
            self.tree_count_label.config(text='')
            return

        def insert(node: dict, parent_iid: str) -> None:
            tags: list[str] = []
            flags: list[str] = []
            if node.get('dev'):
                tags.append('dev')
                flags.append('dev')
            nv = (node.get('name'), node.get('version'))
            vuln_count = vulns_map.get(nv, 0)
            if vuln_count:
                tags.append('vuln')
                flags.append(f'vuln×{vuln_count}')
            iid = self.dep_tree.insert(
                parent_iid, 'end',
                text=node['name'],
                values=(node.get('version', ''), ' '.join(flags)),
                tags=tuple(tags),
                open=(parent_iid == ''),  # ルートのみ開く
            )
            for child in node['children']:
                insert(child, iid)

        for r in roots:
            insert(r, '')
        vuln_total = sum(vulns_map.values())
        text = f'{count} nodes'
        if vuln_total:
            text += f' / {vuln_total} vulns'
        self.tree_count_label.config(text=text)

    def _tree_expand_all(self) -> None:
        def walk(iid: str) -> None:
            self.dep_tree.item(iid, open=True)
            for child in self.dep_tree.get_children(iid):
                walk(child)
        for iid in self.dep_tree.get_children(''):
            walk(iid)

    def _tree_collapse_all(self) -> None:
        for iid in self.dep_tree.get_children(''):
            self.dep_tree.item(iid, open=False)

    # ── npm audit / audit fix ────────────────────────────────────────────────
    def run_npm_audit(self) -> None:
        if not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return
        if not (self.current_project / 'package.json').exists():
            messagebox.showerror('NodeUpdater', 'package.json not found.')
            return
        self.audit_text.delete('1.0', 'end')
        self._set_status('`npm audit --json` を実行中…')
        project_str = str(self.current_project)

        def work():
            return npm_global.run_npm_audit(project_str)

        def done(result, err):
            if err:
                self._set_status(f'Error: {err}', color='#c00')
                return
            if not result:
                self._set_status('npm audit 失敗', color='#c00')
                self.audit_text.insert(
                    'end', 'npm audit を実行できませんでした (npm 未インストール / タイムアウト)。\n'
                )
                return
            self._last_npm_audit = result
            self._render_npm_audit(result)
            meta = (result.get('metadata') or {}).get('vulnerabilities') or {}
            total = meta.get('total') or 0
            self._set_status(f'npm audit: {total} 件の脆弱性')

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
            self._set_status(f'別 console で実行中: {cmd}  (完了後 リロード)')
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

    def _show_changelog(self, scope: str) -> None:
        pkg = self._selected_pkg(scope)
        if not pkg:
            messagebox.showinfo('NodeUpdater', 'Select a package first.')
            return
        latest = (pkg.get('latest') or pkg.get('latestMajor') or pkg.get('latestMinor')
                  or pkg.get('current'))
        ChangelogDialog(
            self,
            package_name=pkg['name'],
            current_version=pkg.get('current'),
            latest_version=latest,
            repo_url=pkg.get('repositoryUrl'),
        )

    def _safe_install_global(self) -> None:
        """未インストールパッケージを cooldown 適用後の版で 1 つずつ安全に追加。

        テーブル選択不要 (まだ install していないパッケージが対象なので)。
        Install 完了後に Global タブを refresh して結果を反映する。
        """
        SafeInstallDialog(
            self,
            title='Safe Install (Global / npm -g)',
            pm='npm',
            global_install=True,
            cwd=None,
            cooldown_days=self._cooldown(),
            resolver=npm_registry.resolve_for_install,
            pkg_manager=pkg_manager,
            opener=npm_global.open_command_prompt,
            on_installed=lambda: self.refresh_global(force=True),
        )

    def _install_selected(self, scope: str, target: str) -> None:
        """scope: 'project' | 'global'   target: 'minor' | 'major'

        複数選択時は 1 つの `npm install` コマンドにまとめて起動する。
        対象更新が無いパッケージはスキップして確認ダイアログで通知。
        """
        table = self.project_table if scope == 'project' else self.global_table
        selected = table.get_selected_all()
        if not selected:
            messagebox.showinfo('NodeUpdater', 'Select one or more packages first.')
            return
        if scope == 'project' and not self.current_project:
            messagebox.showinfo('NodeUpdater', 'Choose a project first.')
            return

        # target:
        #   'wanted' = package.json の spec が許す最高版 (Wanted 列の値)
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
                'NodeUpdater', f'選択された {len(selected)} 件すべてに {label} の更新候補はありません。'
            )
            return

        is_global = (scope == 'global')
        cwd = None if is_global else str(self.current_project)
        specs = [f'{n}@{v}' for n, v in targets]
        # global は常に npm、project は lockfile から PM を検出
        pm = 'npm' if is_global else pkg_manager.detect(self.current_project)
        target_label = {
            'wanted': 'Install Wanted (within spec)',
            'minor': 'Install Minor Up',
            'major': 'Install Major Up',
        }.get(target, target)

        # 履歴用に現行版を取得 (selection から)
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

        cmd = pkg_manager.install_command(pm, specs, global_install=is_global)
        try:
            npm_global.open_command_prompt(cmd, cwd=cwd)
            self._set_status(
                f'Opened prompt [{pm}]: install {len(specs)} package(s) '
                f'({"global" if is_global else "project"})'
            )
            # 履歴記録 (プロジェクトスコープのみ。global は記録先プロジェクトが無いため除外)
            if not is_global and self.current_project:
                history.append(
                    project_path=str(self.current_project),
                    pm=pm,
                    scope=scope,
                    specs=specs,
                    workspace=self._current_workspace,
                    from_versions=from_versions,
                )
        except OSError as e:
            messagebox.showerror('NodeUpdater', f'プロンプトの起動に失敗しました\n\n{e}')


def _is_node_project(path: str) -> bool:
    """Node プロジェクトの判定: package.json がルートにあれば対象。

    state.json は両 GUI で共有しているため、recent_projects ドロップダウンは
    自分の ecosystem のものだけに絞り込んでから表示する。
    """
    return (Path(path) / 'package.json').is_file()


def _build_package_list(deps: list[dict], infos: dict[str, dict]) -> list[dict]:
    """deps と registry info を結合して PackageTable に渡せる形に整形。"""
    out = []
    for d in deps:
        info = infos.get(d['name']) or {}
        latest = info.get('latest')
        latest_minor = info.get('latestMinor')
        latest_major = info.get('latestMajor')
        installed_version = d.get('installed_version')
        # is_installed が明示されていない古いデータは installed_version 有無で判定。
        is_installed = d.get('installed')
        if is_installed is None:
            is_installed = installed_version is not None
        # 「Current」表示は実インストール版。未導入なら None (テーブルが '-' を出す)。
        current_display = installed_version if is_installed else None
        if not is_installed:
            status = 'not_installed'
        else:
            status = semver.classify(installed_version or d.get('version'), latest)
            if latest_minor and latest_major:
                status = 'both'
        # 未導入時は「現在版に紐づく」field をクリアする。spec 正規化版
        # ('^1.2.3' → '1.2.3') を current_version として fetch_one に渡しているため、
        # そのままだと「入っていない版の公開日 / provenance / deprecated」が
        # Current 列の付随情報として表示されてしまう。
        current_pub = info.get('currentPublishedAt') if is_installed else None
        current_age = info.get('currentAgeInDays') if is_installed else None
        deprecated = info.get('deprecated') if is_installed else None
        provenance = info.get('provenance') if is_installed else None
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
            'currentPublishedAt': current_pub,
            'latestPublishedAt': info.get('latestPublishedAt'),
            'latestMinorPublishedAt': info.get('latestMinorPublishedAt'),
            'latestMajorPublishedAt': info.get('latestMajorPublishedAt'),
            'allowedLatestPublishedAt': info.get('allowedLatestPublishedAt'),
            'currentAgeInDays': current_age,
            'latestMinorAgeInDays': info.get('latestMinorAgeInDays'),
            'latestMajorAgeInDays': info.get('latestMajorAgeInDays'),
            'allowedLatestAgeInDays': info.get('allowedLatestAgeInDays'),
            'provenance': provenance,
            'deprecated': deprecated,
            'latestDeprecated': info.get('latestDeprecated'),
            'license': info.get('license'),
            'repositoryUrl': info.get('repositoryUrl'),
        })
    return out
