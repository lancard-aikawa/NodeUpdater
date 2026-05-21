"""デバッグログの閲覧・保存ダイアログ (構造化 JSONL 用 Treeview ベース)。

`shared.debug_log` が書き出した JSON Lines を Treeview に展開して表示する:
  - 親行: 1 entry (timestamp / level / logger / summary)
  - 子行: 各 fields / detail (展開時に表示)
  - フィルタ: level (DEBUG/INFO/WARN/ERROR) + logger + 検索ワード
  - Save as / Clear / Refresh (+ 自動更新 opt-in)
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import debug_log

# Treeview の行色 (level 別)
_LEVEL_TAGS = {
    'DEBUG': '#888888',
    'INFO':  '#000000',
    'WARN':  '#a66000',
    'ERROR': '#c00000',
}
_LEVEL_BG = {
    'WARN':  '#fff3b0',
    'ERROR': '#ffd0d0',
}

# 自動更新間隔 (ms)
_AUTO_REFRESH_MS = 3000


class DebugLogDialog(tk.Toplevel):
    """構造化デバッグログを Treeview で展開表示するダイアログ。"""

    def __init__(self, master, app_name: str = 'PkgUpdater'):
        super().__init__(master)
        self.title(f'{app_name} — Debug Log')
        self.transient(master)
        self.geometry('1100x600')
        self.app_name = app_name

        self._entries: list[dict] = []  # 直近 read_entries 結果
        self._auto_refresh_job: str | None = None

        body = ttk.Frame(self, padding=8)
        body.pack(fill='both', expand=True)

        # ── ヘッダ ─────────────────────────────────────────────────────────
        header = ttk.Frame(body)
        header.pack(fill='x')
        path_str = str(debug_log.log_file_path())
        ttk.Label(
            header, text=f'Log file: {path_str}', foreground='#666',
        ).pack(side='left')

        # ── ボタン行 (下部に先に配置) ─────────────────────────────────────
        bar = ttk.Frame(body)
        bar.pack(side='bottom', fill='x', pady=(8, 0))
        ttk.Button(bar, text='閉じる', command=self.destroy).pack(side='right')
        ttk.Button(bar, text='保存…', command=self._save_as).pack(side='right', padx=(0, 4))
        ttk.Button(bar, text='クリア', command=self._clear).pack(side='right', padx=(0, 4))
        ttk.Button(bar, text='再読込', command=self._refresh).pack(side='left')
        ttk.Button(bar, text='全展開', command=self._expand_all).pack(side='left', padx=(8, 0))
        ttk.Button(bar, text='全折りたたみ', command=self._collapse_all).pack(side='left', padx=(4, 0))
        # 自動更新トグル
        self._auto_refresh_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar, text='自動更新 (3秒)', variable=self._auto_refresh_var,
            command=self._toggle_auto_refresh,
        ).pack(side='left', padx=(12, 0))
        self._count_label = ttk.Label(bar, text='', foreground='#666')
        self._count_label.pack(side='left', padx=(12, 0))

        # ── フィルタバー ─────────────────────────────────────────────────
        filter_bar = ttk.Frame(body)
        filter_bar.pack(side='top', fill='x', pady=(8, 4))
        ttk.Label(filter_bar, text='レベル:').pack(side='left')
        self._level_var = tk.StringVar(value='全て')
        level_combo = ttk.Combobox(
            filter_bar, textvariable=self._level_var, state='readonly',
            values=['全て', 'DEBUG 以上', 'INFO 以上', 'WARN 以上', 'ERROR のみ'],
            width=12,
        )
        level_combo.pack(side='left', padx=(4, 12))
        level_combo.bind('<<ComboboxSelected>>', lambda _e: self._render())

        ttk.Label(filter_bar, text='ロガー:').pack(side='left')
        self._logger_var = tk.StringVar(value='全て')
        self._logger_combo = ttk.Combobox(
            filter_bar, textvariable=self._logger_var, state='readonly',
            values=['全て'], width=24,
        )
        self._logger_combo.pack(side='left', padx=(4, 12))
        self._logger_combo.bind('<<ComboboxSelected>>', lambda _e: self._render())

        ttk.Label(filter_bar, text='検索:').pack(side='left')
        self._search_var = tk.StringVar()
        ttk.Entry(filter_bar, textvariable=self._search_var, width=24).pack(side='left', padx=(4, 0))
        self._search_var.trace_add('write', lambda *_: self._render())

        # ── 本体 (Treeview) ──────────────────────────────────────────────
        tree_frame = ttk.Frame(body)
        tree_frame.pack(side='top', fill='both', expand=True)
        columns = ('time', 'level', 'logger', 'summary')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='tree headings')
        self.tree.heading('#0', text='')
        self.tree.heading('time', text='Time')
        self.tree.heading('level', text='Level')
        self.tree.heading('logger', text='Logger')
        self.tree.heading('summary', text='Summary')
        self.tree.column('#0', width=24, stretch=False)
        self.tree.column('time', width=160, anchor='w', stretch=False)
        self.tree.column('level', width=60, anchor='center', stretch=False)
        self.tree.column('logger', width=200, anchor='w', stretch=False)
        self.tree.column('summary', width=600, anchor='w', stretch=True)
        # level 別 tag (foreground + 強調 background)
        for lv, color in _LEVEL_TAGS.items():
            self.tree.tag_configure(lv, foreground=color)
        for lv, bg in _LEVEL_BG.items():
            self.tree.tag_configure(f'{lv}_bg', foreground=_LEVEL_TAGS[lv], background=bg)
        # 子行 (detail/fields) は薄色フォントで区別
        self.tree.tag_configure('child', foreground='#555')

        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.bind('<Escape>', lambda _e: self.destroy())
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.after(50, self.grab_set)
        self._refresh()

    # ── データロード ─────────────────────────────────────────────────────
    def _refresh(self) -> None:
        self._entries = debug_log.read_entries()
        # logger 一覧を更新 (現在の選択を保持)
        loggers = sorted({(e.get('logger') or '?') for e in self._entries})
        current = self._logger_var.get()
        values = ['全て'] + loggers
        self._logger_combo['values'] = values
        if current not in values:
            self._logger_var.set('全て')
        self._render()

    # ── フィルタ適用 + 描画 ──────────────────────────────────────────────
    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        min_level = self._min_level_from_var()
        logger_filter = self._logger_var.get()
        query = (self._search_var.get() or '').lower().strip()

        shown = 0
        # 新しいログを上に出す (新→古の順)
        for entry in reversed(self._entries):
            level = (entry.get('level') or 'INFO').upper()
            if not _level_passes(level, min_level):
                continue
            if logger_filter != '全て' and entry.get('logger') != logger_filter:
                continue
            if query and not _matches_query(entry, query):
                continue
            self._insert_entry(entry)
            shown += 1
        total = len(self._entries)
        self._count_label.config(text=f'{shown} / {total} 件')

    def _insert_entry(self, entry: dict) -> None:
        level = (entry.get('level') or 'INFO').upper()
        tag = f'{level}_bg' if level in _LEVEL_BG else level
        parent = self.tree.insert(
            '', 'end',
            values=(
                _short_time(entry.get('ts') or ''),
                level,
                entry.get('logger') or '',
                entry.get('summary') or '',
            ),
            tags=(tag,),
        )
        # 子行: fields (key=value を 1 行ずつ) → detail (各 detail key を 1 行ずつ)
        fields = entry.get('fields') or {}
        for key, value in fields.items():
            self.tree.insert(
                parent, 'end',
                values=('', '', key, _short_value(value)),
                tags=('child',),
            )
        detail = entry.get('detail') or {}
        for key, value in detail.items():
            # detail は長文の可能性 → 改行入りは「先頭 1 行 + (N 行)」表示
            text = _format_detail_value(value)
            self.tree.insert(
                parent, 'end',
                values=('', '', f'detail.{key}', text),
                tags=('child',),
            )

    # ── ボタンアクション ─────────────────────────────────────────────────
    def _expand_all(self) -> None:
        for iid in self.tree.get_children(''):
            self.tree.item(iid, open=True)

    def _collapse_all(self) -> None:
        for iid in self.tree.get_children(''):
            self.tree.item(iid, open=False)

    def _save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension='.jsonl',
            initialfile=f'{self.app_name.lower()}-debug.jsonl',
            filetypes=[('JSON Lines', '*.jsonl'), ('Log files', '*.log'),
                       ('Text files', '*.txt'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                for entry in self._entries:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
            messagebox.showinfo(self.app_name, f'保存しました:\n{path}')
        except OSError as e:
            messagebox.showerror(self.app_name, f'書き込みエラー\n\n{e}')

    def _clear(self) -> None:
        if not messagebox.askyesno(
            self.app_name, 'デバッグログをすべて削除しますか?',
        ):
            return
        debug_log.clear()
        self._refresh()

    def _toggle_auto_refresh(self) -> None:
        if self._auto_refresh_var.get():
            self._schedule_auto_refresh()
        else:
            self._cancel_auto_refresh()

    def _schedule_auto_refresh(self) -> None:
        self._cancel_auto_refresh()
        self._auto_refresh_job = self.after(_AUTO_REFRESH_MS, self._auto_tick)

    def _cancel_auto_refresh(self) -> None:
        if self._auto_refresh_job is not None:
            try:
                self.after_cancel(self._auto_refresh_job)
            except tk.TclError:
                pass
            self._auto_refresh_job = None

    def _auto_tick(self) -> None:
        self._refresh()
        if self._auto_refresh_var.get():
            self._schedule_auto_refresh()

    def _on_close(self) -> None:
        self._cancel_auto_refresh()
        self.destroy()

    # ── helpers ──────────────────────────────────────────────────────────
    def _min_level_from_var(self) -> str | None:
        v = self._level_var.get()
        if v == '全て' or v == 'DEBUG 以上':
            return 'DEBUG'
        if v == 'INFO 以上':
            return 'INFO'
        if v == 'WARN 以上':
            return 'WARN'
        if v == 'ERROR のみ':
            return 'ERROR'
        return None


_LEVEL_ORDER = {'DEBUG': 0, 'INFO': 1, 'WARN': 2, 'ERROR': 3}


def _level_passes(level: str, min_level: str | None) -> bool:
    if min_level is None:
        return True
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(min_level, 0)


def _matches_query(entry: dict, query: str) -> bool:
    """logger / summary / fields / detail を query で全文検索 (大文字小文字無視)。"""
    if query in (entry.get('logger') or '').lower():
        return True
    if query in (entry.get('summary') or '').lower():
        return True
    fields = entry.get('fields') or {}
    for k, v in fields.items():
        if query in str(k).lower() or query in str(v).lower():
            return True
    detail = entry.get('detail') or {}
    for k, v in detail.items():
        if query in str(k).lower() or query in str(v).lower():
            return True
    return False


def _short_time(ts: str) -> str:
    """ISO 8601 タイムスタンプから HH:MM:SS 部分を抜く。失敗時は原文。"""
    if not ts or 'T' not in ts:
        return ts
    # '2026-05-21T19:30:44+09:00' → '19:30:44'
    try:
        t = ts.split('T', 1)[1]
        return t.split('+', 1)[0].split('-', 1)[0][:8]  # naive 取り扱い
    except (ValueError, IndexError):
        return ts


def _short_value(v) -> str:
    """fields の値を 1 行に丸める。"""
    if v is None:
        return ''
    s = str(v)
    if len(s) > 200:
        s = s[:197] + '…'
    return s.replace('\n', '\\n')


def _format_detail_value(v) -> str:
    """detail の値を Tree セル用に整形。長文は 1 行に丸めて (... N行) を付ける。"""
    if v is None:
        return ''
    if isinstance(v, (dict, list)):
        try:
            s = json.dumps(v, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            s = str(v)
    else:
        s = str(v)
    lines = s.splitlines()
    if len(lines) > 1:
        head = lines[0]
        s = f'{head}  …({len(lines)}行)'
    if len(s) > 400:
        s = s[:397] + '…'
    return s
