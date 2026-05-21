"""デバッグログの閲覧・保存ダイアログ。

`shared.debug_log` が書き出した append-only ログを末尾から読んで表示する。
両 GUI (NodeUpdater / PypkgUpdater) から共通で使う。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import debug_log


class DebugLogDialog(tk.Toplevel):
    """末尾 N byte を読み込んで表示。Save as / Clear / Refresh ボタン付き。"""

    def __init__(self, master, app_name: str = 'PkgUpdater'):
        super().__init__(master)
        self.title(f'{app_name} — Debug Log')
        self.transient(master)
        self.geometry('900x520')
        self.app_name = app_name

        body = ttk.Frame(self, padding=8)
        body.pack(fill='both', expand=True)

        # ── ヘッダ ─────────────────────────────────────────────────────────
        header = ttk.Frame(body)
        header.pack(fill='x')
        path_str = str(debug_log.log_file_path())
        ttk.Label(
            header, text=f'Log file: {path_str}', foreground='#666',
        ).pack(side='left')

        # ── ボタン行 (下部に先に配置すると expand=True の本体が空間を食ってもボタンが残る) ─────
        bar = ttk.Frame(body)
        bar.pack(side='bottom', fill='x', pady=(8, 0))
        ttk.Button(bar, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(bar, text='Save as…', command=self._save_as).pack(side='right', padx=(0, 4))
        ttk.Button(bar, text='Clear', command=self._clear).pack(side='right', padx=(0, 4))
        ttk.Button(bar, text='Refresh', command=self._refresh).pack(side='left')

        # ── 本文 (上下 + 左右スクロール可能) ──────────────────────────────────
        # 1 行が長い (Windows pathや JSON 等) のでデバッグログは横スクロール必須。
        # wrap='none' と xscrollcommand を組合せて、(text, vsb, hsb) を grid 配置。
        text_frame = ttk.Frame(body)
        text_frame.pack(side='top', fill='both', expand=True, pady=(8, 0))
        self.text = tk.Text(
            text_frame, wrap='none', state='disabled', font=('TkFixedFont', 9),
        )
        vsb = ttk.Scrollbar(text_frame, orient='vertical', command=self.text.yview)
        hsb = ttk.Scrollbar(text_frame, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.text.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        self.bind('<Escape>', lambda _e: self.destroy())
        self.after(50, self.grab_set)
        self._refresh()

    def _refresh(self) -> None:
        content = debug_log.read_text()
        self.text.config(state='normal')
        self.text.delete('1.0', 'end')
        if not content:
            self.text.insert('end', '(ログは空です。subprocess の失敗等が起きると自動で書き込まれます)')
        else:
            self.text.insert('end', content)
            self.text.see('end')  # 末尾にスクロール
        self.text.config(state='disabled')

    def _save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension='.log',
            initialfile=f'{self.app_name.lower()}-debug.log',
            filetypes=[('Log files', '*.log'), ('Text files', '*.txt'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            content = self.text.get('1.0', 'end')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
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
