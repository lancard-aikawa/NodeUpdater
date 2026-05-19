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

    def __init__(self, master, on_select=None, on_render=None):
        super().__init__(master)
        self.on_select = on_select
        self.on_render = on_render

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
        # フィルタ前の完全データ。set_filter で再描画する際に使う
        self._all_packages: list[dict] = []
        self._filter_query = ''
        self._filter_status: str | None = None  # None=全件、'outdated' は major/minor/both 集約
        self._filter_dev_only = False

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
        self._all_packages = sorted(
            packages, key=lambda p: _STATUS_ORDER.get(p.get('status', 'unknown'), 9)
        )
        self._render()

    def set_filter(
        self,
        query: str = '',
        status: str | None = None,
        dev_only: bool = False,
    ) -> None:
        """フィルタ条件を更新して再描画。set_packages を呼び直す必要はない。"""
        self._filter_query = (query or '').lower().strip()
        self._filter_status = status
        self._filter_dev_only = bool(dev_only)
        self._render()

    def _matches_filter(self, p: dict) -> bool:
        if self._filter_query:
            name = (p.get('name') or '').lower()
            if self._filter_query not in name:
                return False
        if self._filter_dev_only and not p.get('dev'):
            return False
        st = p.get('status', 'unknown')
        if self._filter_status:
            if self._filter_status == 'outdated':
                if st not in ('major', 'minor', 'both'):
                    return False
            elif st != self._filter_status:
                return False
        return True

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_data.clear()
        for p in self._all_packages:
            if not self._matches_filter(p):
                continue
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
        if self.on_render:
            self.on_render(len(self._row_data), len(self._all_packages))

    def visible_count(self) -> tuple[int, int]:
        """(表示中, 全体) を返す。"""
        return (len(self._row_data), len(self._all_packages))

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
