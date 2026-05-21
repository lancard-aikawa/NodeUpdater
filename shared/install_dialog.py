"""Install 確認 + Dry-run プレビュー統合 Toplevel ダイアログ。

ユーザーは確認前に Dry-run を実行して影響範囲 (追加/更新されるパッケージ、
警告など) を見てから本実行できる。Install ボタンで従来通り新規 cmd
プロンプトを起動して進捗が残るようにする。

ecosystem 中立: pkg_manager (npm/yarn/pnpm/bun または pip/uv/poetry/pipenv)
を構築時に渡すモジュールから取得する。インターフェース要件は以下:
  install_command(pm, specs, global_install=, dry_run=) -> str
  run_dry_run(pm, specs, cwd=, global_install=, timeout=) -> (stdout, stderr, rc)
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any


class InstallDialog(tk.Toplevel):
    def __init__(
        self,
        master,
        title_label: str,
        specs: list[str],
        skipped: list[str],
        cwd: str | None,
        global_install: bool,
        pm: str,
        pkg_manager: Any,
    ):
        super().__init__(master)
        self.title('Install Preview')
        self.transient(master)
        self.geometry('820x520')
        self.result: str | None = None  # 'install' で本実行を要求

        self.specs = specs
        self.skipped = skipped
        self.cwd = cwd
        self.global_install = global_install
        self.pm = pm
        self.pkg_manager = pkg_manager

        install_cmd = pkg_manager.install_command(pm, specs, global_install=global_install)
        dry_cmd = pkg_manager.install_command(
            pm, specs, global_install=global_install, dry_run=True
        )

        body = ttk.Frame(self, padding=8)
        body.pack(fill='both', expand=True)

        ttk.Label(
            body,
            text=f'{title_label}  —  {len(specs)} package(s)  [{pm}]',
            font=('TkDefaultFont', 10, 'bold'),
        ).pack(anchor='w')

        info = ttk.Frame(body)
        info.pack(fill='x', pady=(6, 0))
        ttk.Label(info, text='Install:', foreground='#666').grid(row=0, column=0, sticky='w')
        ttk.Label(info, text=install_cmd, font=('TkFixedFont', 9)).grid(row=0, column=1, sticky='w', padx=(4, 0))
        ttk.Label(info, text='Dry-run:', foreground='#666').grid(row=1, column=0, sticky='w')
        ttk.Label(info, text=dry_cmd, font=('TkFixedFont', 9)).grid(row=1, column=1, sticky='w', padx=(4, 0))
        ttk.Label(info, text='Cwd:', foreground='#666').grid(row=2, column=0, sticky='w')
        ttk.Label(info, text=cwd or '(default)', font=('TkFixedFont', 9)).grid(row=2, column=1, sticky='w', padx=(4, 0))

        if skipped:
            shown = ', '.join(skipped[:5])
            if len(skipped) > 5:
                shown += f' ほか {len(skipped) - 5} 件'
            ttk.Label(
                body, text=f'Skipped (no target version): {shown}', foreground='#a60',
            ).pack(anchor='w', pady=(6, 0))

        ttk.Label(
            body, text='Dry-run output:', foreground='#666',
        ).pack(anchor='w', pady=(8, 2))

        text_frame = ttk.Frame(body)
        text_frame.pack(fill='both', expand=True)
        self.text = tk.Text(
            text_frame, wrap='word', height=15, state='disabled',
            font=('TkFixedFont', 9),
        )
        self.text.pack(side='left', fill='both', expand=True)
        vsb = ttk.Scrollbar(text_frame, orient='vertical', command=self.text.yview)
        vsb.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=vsb.set)
        self._set_text('(Click "Run Dry-Run" to preview)')

        btns = ttk.Frame(body)
        btns.pack(fill='x', pady=(8, 0))
        self.dry_btn = ttk.Button(btns, text='Run Dry-Run', command=self._run_dry_run)
        self.dry_btn.pack(side='left')
        ttk.Button(btns, text='Cancel', command=self.destroy).pack(side='right', padx=(4, 0))
        ttk.Button(btns, text='Install (new console)', command=self._install).pack(side='right')

        self.bind('<Escape>', lambda _e: self.destroy())
        self.after(50, self.grab_set)

    def _set_text(self, content: str) -> None:
        self.text.config(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('end', content)
        self.text.config(state='disabled')

    def _run_dry_run(self) -> None:
        self.dry_btn.config(state='disabled')
        self._set_text('Running dry-run...\n')

        def work():
            stdout, stderr, rc = self.pkg_manager.run_dry_run(
                self.pm, self.specs, cwd=self.cwd, global_install=self.global_install,
            )
            self.after(0, lambda: self._show_result(stdout, stderr, rc))

        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, stdout: str, stderr: str, rc: int) -> None:
        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip())
        if stderr:
            parts.append('\n--- stderr ---\n' + stderr.rstrip())
        parts.append(f'\n--- exit code: {rc} ---')
        self._set_text('\n'.join(parts))
        self.dry_btn.config(state='normal')

    def _install(self) -> None:
        self.result = 'install'
        self.destroy()
