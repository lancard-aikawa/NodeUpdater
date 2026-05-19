"""依存パッケージ一覧テーブル (ttk.Treeview ベース)。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# status → 行タグ（色分け）
_STATUS_COLORS = {
    'both':    '#ffd0d0',  # 赤系: major + minor 両方ある
    'major':   '#ffe0c0',  # オレンジ: メジャー更新あり
    'minor':   '#fff3b0',  # 黄: マイナー/パッチ更新あり
    'latest':  '#d8f3d0',  # 緑: 最新
    'unknown': '#e8e8e8',  # グレー: 不明
}

_STATUS_ORDER = {'both': 0, 'major': 1, 'minor': 2, 'unknown': 3, 'latest': 4}


class PackageTable(ttk.Frame):
    """Package / Current+Age / Minor+Age / Major+Age / Status / dev / provenance を表示。

    Age 列はそれぞれ左隣のバージョンに対応する公開後経過日数。
    """

    COLUMNS = ('name', 'current', 'age_cur', 'minor', 'age_min', 'major', 'age_maj',
               'status', 'dev', 'prov', 'dep', 'license')

    def __init__(self, master, on_select=None):
        super().__init__(master)
        self.on_select = on_select

        tree = ttk.Treeview(self, columns=self.COLUMNS, show='headings', selectmode='browse')
        # Age 列はヘッダ短縮: status 用語 (minor/major) に揃えて Cur/Min/Maj。
        # dep: 'yes' (現行版が deprecated) / 'abnd' (package abandoned: latest も deprecated) / ''
        headings = {
            'name':    ('Package',          220),
            'current': ('Current',           80),
            'age_cur': ('Cur age',           55),
            'minor':   ('Minor up',          90),
            'age_min': ('Min age',           55),
            'major':   ('Major up',          90),
            'age_maj': ('Maj age',           55),
            'status':  ('Status',            70),
            'dev':     ('dev',               40),
            'prov':    ('prov',              50),
            'dep':     ('dep',               50),
            'license': ('License',           90),
        }
        right_aligned = {'age_cur', 'age_min', 'age_maj'}
        centered = {'dev', 'prov', 'status', 'dep'}
        for col, (label, width) in headings.items():
            tree.heading(col, text=label)
            if col in right_aligned:
                anchor = 'e'
            elif col in centered:
                anchor = 'center'
            else:
                anchor = 'w'
            tree.column(col, width=width, anchor=anchor, stretch=(col == 'name'))

        for status, color in _STATUS_COLORS.items():
            tree.tag_configure(status, background=color)

        vsb = ttk.Scrollbar(self, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        tree.bind('<<TreeviewSelect>>', self._on_select)
        self.tree = tree
        # 行 iid → package dict のマップ（選択行の完全データ取得用）
        self._row_data: dict[str, dict] = {}

    @staticmethod
    def _age_text(days) -> str:
        if days is None or days == '':
            return ''
        return f'{days}d'

    @staticmethod
    def _dep_text(p: dict) -> str:
        if p.get('deprecated'):
            return 'yes'
        if p.get('latestDeprecated'):
            return 'abnd'
        return ''

    def set_packages(self, packages: list[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_data.clear()
        ordered = sorted(packages, key=lambda p: _STATUS_ORDER.get(p.get('status', 'unknown'), 9))
        for p in ordered:
            iid = self.tree.insert('', 'end', values=(
                p.get('name', ''),
                p.get('current') or '-',
                self._age_text(p.get('currentAgeInDays')),
                p.get('latestMinor') or '',
                self._age_text(p.get('latestMinorAgeInDays')),
                p.get('latestMajor') or '',
                self._age_text(p.get('latestMajorAgeInDays')),
                p.get('status', ''),
                'yes' if p.get('dev') else '',
                'yes' if p.get('provenance') else ('no' if p.get('provenance') is False else ''),
                self._dep_text(p),
                p.get('license') or '',
            ), tags=(p.get('status', 'unknown'),))
            self._row_data[iid] = p

    def get_selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._row_data.get(sel[0])

    def _on_select(self, _event) -> None:
        if not self.on_select:
            return
        pkg = self.get_selected()
        if pkg:
            self.on_select(pkg)
