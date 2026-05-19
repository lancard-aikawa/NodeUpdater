"""プロジェクト単位の更新履歴 (install 試行) を表示する Toplevel。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import history


class HistoryDialog(tk.Toplevel):
    def __init__(self, master, project_path):
        super().__init__(master)
        self.title('Update History')
        self.transient(master)
        self.geometry('820x520')
        self.project_path = project_path

        body = ttk.Frame(self, padding=8)
        body.pack(fill='both', expand=True)

        header = ttk.Frame(body)
        header.pack(fill='x')
        ttk.Label(
            header, text=f'Project: {project_path}', font=('TkDefaultFont', 10, 'bold'),
        ).pack(side='left')
        ttk.Button(header, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(header, text='Clear history', command=self._clear).pack(side='right', padx=(0, 4))

        # ── ツリー (新しい順) ──────────────────────────────────────────────
        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill='both', expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(
            tree_frame,
            columns=('ts', 'pm', 'scope', 'workspace', 'count', 'specs'),
            show='headings',
            selectmode='browse',
        )
        cols = {
            'ts': ('When', 150),
            'pm': ('PM', 55),
            'scope': ('Scope', 70),
            'workspace': ('Workspace', 110),
            'count': ('#', 35),
            'specs': ('Specs', 380),
        }
        for col, (label, width) in cols.items():
            self.tree.heading(col, text=label)
            anchor = 'e' if col == 'count' else 'w'
            self.tree.column(col, width=width, anchor=anchor,
                             stretch=(col == 'specs'))
        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # ── 詳細表示 ──────────────────────────────────────────────────────
        self.detail = tk.Text(body, height=8, wrap='word', state='disabled',
                              font=('TkFixedFont', 9))
        self.detail.pack(fill='x', pady=(8, 0))
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        self.bind('<Escape>', lambda _e: self.destroy())
        self.after(50, self.grab_set)
        self._refresh()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._entries = list(reversed(history.read(self.project_path)))
        for i, e in enumerate(self._entries):
            specs = e.get('specs') or []
            specs_text = ', '.join(specs[:3])
            if len(specs) > 3:
                specs_text += f'  (+{len(specs) - 3} more)'
            self.tree.insert(
                '', 'end', iid=str(i),
                values=(
                    e.get('ts', '').replace('T', ' '),
                    e.get('pm', ''),
                    e.get('scope', ''),
                    e.get('workspace', '') or '(root)',
                    len(specs),
                    specs_text,
                ),
            )
        if not self._entries:
            self._set_detail('(履歴なし — Install を実行するとここに記録されます)')

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except (TypeError, ValueError):
            return
        e = self._entries[idx]
        lines: list[str] = []
        lines.append(f'When     : {e.get("ts", "")}')
        lines.append(f'PM       : {e.get("pm", "")}')
        lines.append(f'Scope    : {e.get("scope", "")}')
        lines.append(f'Workspace: {e.get("workspace", "") or "(root)"}')
        from_versions = e.get('from') or {}
        lines.append('')
        lines.append('Targets:')
        for spec in e.get('specs') or []:
            name = spec.split('@')[0] if not spec.startswith('@') else '@' + spec.split('@')[1]
            prev = from_versions.get(name)
            arrow = f'  (from {prev})' if prev else ''
            lines.append(f'  {spec}{arrow}')
        self._set_detail('\n'.join(lines))

    def _set_detail(self, text: str) -> None:
        self.detail.config(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('end', text)
        self.detail.config(state='disabled')

    def _clear(self) -> None:
        if not messagebox.askyesno(
            'NodeUpdater', 'このプロジェクトの履歴をすべて削除しますか？',
        ):
            return
        history.clear(self.project_path)
        self._refresh()
        self._set_detail('(履歴を削除しました)')
