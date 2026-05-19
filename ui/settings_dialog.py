"""アプリ全般の設定 (registry URL / proxy / OSV API URL / 並列数) ダイアログ。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core import state


class SettingsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title('Settings')
        self.transient(master)
        self.resizable(False, False)

        # ── 入力欄 ───────────────────────────────────────────────────────────
        self.registry_var = tk.StringVar(value=state.get_registry_url())
        self.proxy_var = tk.StringVar(value=state.get_proxy_url())
        self.osv_var = tk.StringVar(value=state.get_osv_api_url())
        self.parallel_var = tk.IntVar(value=state.get_parallel_requests())

        body = ttk.Frame(self, padding=12)
        body.pack(fill='both', expand=True)

        row = 0
        ttk.Label(body, text='npm Registry URL:').grid(row=row, column=0, sticky='w')
        row += 1
        ttk.Entry(body, textvariable=self.registry_var, width=56).grid(
            row=row, column=0, columnspan=2, sticky='ew', pady=(0, 8)
        )

        row += 1
        ttk.Label(body, text='HTTP/HTTPS Proxy URL (空欄で未使用):').grid(row=row, column=0, sticky='w')
        row += 1
        ttk.Entry(body, textvariable=self.proxy_var, width=56).grid(
            row=row, column=0, columnspan=2, sticky='ew', pady=(0, 8)
        )

        row += 1
        ttk.Label(body, text='OSV API URL:').grid(row=row, column=0, sticky='w')
        row += 1
        ttk.Entry(body, textvariable=self.osv_var, width=56).grid(
            row=row, column=0, columnspan=2, sticky='ew', pady=(0, 8)
        )

        row += 1
        ttk.Label(body, text='並列リクエスト数 (npm registry, 1〜32):').grid(row=row, column=0, sticky='w')
        row += 1
        ttk.Spinbox(
            body, from_=1, to=32, width=6, textvariable=self.parallel_var,
        ).grid(row=row, column=0, sticky='w', pady=(0, 12))

        # ── ボタン ────────────────────────────────────────────────────────────
        btns = ttk.Frame(body)
        btns.grid(row=row + 1, column=0, columnspan=2, sticky='ew')
        btns.columnconfigure(0, weight=1)
        ttk.Button(btns, text='Reset to defaults', command=self._reset).grid(row=0, column=0, sticky='w')
        ttk.Button(btns, text='Cancel', command=self.destroy).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(btns, text='Save', command=self._save).grid(row=0, column=2)

        body.columnconfigure(0, weight=1)

        self.bind('<Escape>', lambda _e: self.destroy())
        self.after(50, self.grab_set)  # モーダルにしてフォーカス確保

    def _reset(self) -> None:
        self.registry_var.set(state.DEFAULT_REGISTRY_URL)
        self.proxy_var.set('')
        self.osv_var.set(state.DEFAULT_OSV_API_URL)
        self.parallel_var.set(state.DEFAULT_PARALLEL_REQUESTS)

    def _save(self) -> None:
        # デフォルト値と同一なら state からも消す (キー未設定の方が将来のデフォルト変更を拾える)
        reg = self.registry_var.get().strip()
        state.set_setting('registry_url', '' if reg == state.DEFAULT_REGISTRY_URL else reg)
        state.set_setting('proxy_url', self.proxy_var.get().strip())
        osv_url = self.osv_var.get().strip()
        state.set_setting('osv_api_url', '' if osv_url == state.DEFAULT_OSV_API_URL else osv_url)
        try:
            parallel = max(1, min(32, int(self.parallel_var.get())))
        except (TypeError, ValueError):
            parallel = state.DEFAULT_PARALLEL_REQUESTS
        state.set_setting(
            'parallel_requests',
            '' if parallel == state.DEFAULT_PARALLEL_REQUESTS else parallel,
        )
        self.destroy()
