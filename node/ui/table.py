"""依存パッケージ一覧テーブル (ttk.Treeview ベース)。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from shared import ui_tooltip

# status → 行タグ（色分け）
_STATUS_COLORS = {
    'both':          '#ffd0d0',  # 赤系: major + minor 両方ある
    'major':         '#ffe0c0',  # オレンジ: メジャー更新あり
    'minor':         '#fff3b0',  # 黄: マイナー/パッチ更新あり
    'latest':        '#d8f3d0',  # 緑: 最新
    'not_installed': '#d6e6f5',  # 青: package.json にあるが node_modules に無い
    'unknown':       '#e8e8e8',  # グレー: 不明
}

# ソート優先順: 重大度の高い更新が先頭。未インストールは latest より前 (操作対象として目立たせる)。
_STATUS_ORDER = {
    'both': 0, 'major': 1, 'minor': 2,
    'not_installed': 3, 'unknown': 4, 'latest': 5,
}

# Status セル表示用のラベル (狭い列に長文を入れないため短縮+日本語)
_STATUS_LABEL = {
    'both':          'both',
    'major':         'major',
    'minor':         'minor',
    'latest':        'latest',
    'not_installed': '未導入',
    'unknown':       'unknown',
}

# Wanted セルが解釈不能 spec の時に入れる sentinel 文字列。
# npm_registry.fetch_one 側と取り決め。表示はそのまま `?` として出す。
UNPARSEABLE_SPEC_MARKER = '?'

# 列構成プリセット。COLUMNS は維持し displaycolumns で表示分のみ切替。
# Project タブ用 (spec があるので Wanted 列が意味を持つ)。
VIEW_PRESETS: dict[str, tuple[str, ...]] = {
    'Wanted': ('name', 'current', 'wanted', 'age_wnt', 'status', 'dev'),
    'Latest': ('name', 'current', 'age_cur', 'minor', 'age_min',
               'major', 'age_maj', 'status'),
    'Audit':  ('name', 'current', 'prov', 'dep', 'license', 'dev'),
    'All':    ('name', 'current', 'age_cur', 'wanted', 'age_wnt',
               'minor', 'age_min', 'major', 'age_maj',
               'status', 'dev', 'prov', 'dep', 'license', 'gz'),
}

# Global タブ用 (`npm list -g` の結果には spec が無いので Wanted を非表示)。
# Wanted preset 自体を外し、All からも wanted/age_wnt を除外する。
VIEW_PRESETS_GLOBAL: dict[str, tuple[str, ...]] = {
    'Latest': ('name', 'current', 'age_cur', 'minor', 'age_min',
               'major', 'age_maj', 'status'),
    'Audit':  ('name', 'current', 'prov', 'dep', 'license', 'dev'),
    'All':    ('name', 'current', 'age_cur',
               'minor', 'age_min', 'major', 'age_maj',
               'status', 'dev', 'prov', 'dep', 'license', 'gz'),
}

# 各プリセットの説明 (View ラジオのツールチップ用)。
VIEW_PRESET_DESCRIPTIONS: dict[str, str] = {
    'Wanted': (
        'spec (^X.Y.Z / ~X.Y.Z / range) が許す最高安定版 (Wanted) を中心に表示。\n'
        '`1.2.3` のような exact pin は Wanted 空欄 (上げ余地なし)。\n'
        '日常運用向け。Global タブでは spec が無いので Wanted は常に空。'
    ),
    'Latest': (
        'spec を無視した「同 major 内最新 (Minor up)」と「次 major (Major up)」を表示。\n'
        'spec を見直して大きく上げる時用。'
    ),
    'Audit': (
        'provenance / deprecated / License / dev を表示。\n'
        '供給チェーン (provenance) や非推奨パッケージ確認用。'
    ),
    'All': (
        '全 15 列 (bundle size 含む) を展開。\n'
        '画面幅によっては横スクロールが出ます。トラブルシュート用。'
    ),
}

DEFAULT_PRESET = 'Wanted'

# ヘッダ色チップ: セル単位 background が ttk.Treeview で出来ない代わりに、
# 列ヘッダ左に小さい色チップ画像を入れて「どのレーンか」を視覚識別する。
# PhotoImage は GC されると黒化するので PackageTable インスタンスに参照を保持する。
#   wanted = 緑 (spec 内なので安全)
#   minor  = 黄 (同 major 内)
#   major  = 橙 (Breaking の可能性)
_HEADING_SWATCH_COLORS: dict[str, str] = {
    'wanted': '#5fbf5f',
    'minor':  '#e6c440',
    'major':  '#e08a3c',
}
_HEADING_SWATCH_SIZE = 10


def _ensure_heading_image_on_left() -> None:
    """Treeview.Heading の layout を「画像 → text」順 (画像が左) に上書き。

    ttk.Treeview のデフォルト layout (vista テーマ等) は heading の image を
    text の右側に置くため、color swatch が次の列の手前に並んで見えてしまう。
    `side='left'` に変更してヘッダ内で「[chip] text」の順になるようにする。

    ttk.Style はプロセスグローバルなので 1 度呼べば両 PackageTable に効く。
    画像を持たないヘッダ (dep_tree など) には視覚的影響なし。
    """
    style = ttk.Style()
    try:
        style.layout('Treeview.Heading', [
            ('Treeview.Heading.cell', {'sticky': 'nswe'}),
            ('Treeview.Heading.border', {'sticky': 'nswe', 'children': [
                ('Treeview.Heading.padding', {'sticky': 'nswe', 'children': [
                    ('Treeview.Heading.image', {'side': 'left', 'sticky': ''}),
                    ('Treeview.Heading.text', {'sticky': 'we'}),
                ]}),
            ]}),
        ])
    except tk.TclError:
        pass  # 一部テーマでは layout 操作不可

# テーブル列ヘッダの日本語説明 (ホバーで表示)。npm CLI 用語との対応を残すため
# ヘッダ自体は英語のままにしている。
_HEADER_TOOLTIPS: dict[str, str] = {
    'name':    'Package: パッケージ名',
    'current': 'Current: 実際にインストールされている版 (node_modules / npm list -g より)。\n'
               'package.json に書いてあるだけで未インストールなら "-" 表示。',
    'age_cur': 'Cur age: Current 版が公開されてからの経過日数',
    'wanted': 'Wanted: package.json の spec (^/~/range) を満たす最高安定版。\n'
              '"-" = 上げ余地なし。 "?" = spec を解釈できず判定不能 (hover で詳細)。',
    'age_wnt': 'Wnt age: Wanted 版が公開されてからの経過日数',
    'minor':   'Minor up: spec を無視した同 major 内の最新版',
    'age_min': 'Min age: Minor 版が公開されてからの経過日数',
    'major':   'Major up: spec を無視した次 major 以降の最新版 (Breaking Change の可能性)',
    'age_maj': 'Maj age: Major 版が公開されてからの経過日数',
    'status':  '状態:\n'
               '  major / minor / both = 更新候補あり\n'
               '  latest = 最新\n'
               '  未導入 = package.json にあるが node_modules に無い\n'
               '  unknown = 判定不能',
    'dev':     'dev: devDependencies のパッケージかどうか',
    'prov':    'prov (provenance): npm が検証する供給元署名の有無',
    'dep':     'dep (deprecated):\n'
               '  yes = 現行版が非推奨\n'
               '  abnd = パッケージ自体が放棄 (latest も非推奨)',
    'license': 'License: SPDX 識別子',
    'gz':      'Bundle (gz): bundlephobia から取得した gzip 後サイズ',
}


class PackageTable(ttk.Frame):
    """Package / Current / Wanted / Minor / Major / Age 列 / status / dev / prov / dep / license / gz。

    Wanted 列は package.json の spec (`^X.Y.Z` / `~X.Y.Z` / range) を満たす最高安定版。
    spec が無い行 (Global タブ / `*`) では Wanted=絶対最新、URL/file/`||` 等は `?` を出す。
    """

    COLUMNS = ('name', 'current', 'age_cur',
               'wanted', 'age_wnt',
               'minor', 'age_min', 'major', 'age_maj',
               'status', 'dev', 'prov', 'dep', 'license', 'gz')

    def __init__(self, master, on_select=None, on_render=None, presets=None):
        super().__init__(master)
        self.on_select = on_select
        self.on_render = on_render
        # presets が None なら module 既定 (Project 用 VIEW_PRESETS) を使う。
        # Global タブは VIEW_PRESETS_GLOBAL を渡して Wanted を非表示にする。
        self.presets: dict[str, tuple[str, ...]] = presets if presets is not None else VIEW_PRESETS

        # 色チップを text の左に出すため layout を 1 度だけ上書き
        _ensure_heading_image_on_left()

        tree = ttk.Treeview(self, columns=self.COLUMNS, show='headings', selectmode='extended')
        # Age 列はヘッダ短縮: status 用語 (minor/major) に揃えて Cur/Min/Maj。
        # dep: 'yes' (現行版が deprecated) / 'abnd' (package abandoned: latest も deprecated) / ''
        # ヘッダは npm CLI 用語 (Wanted/Minor up 等) に合わせて英語のまま。
        # 日本語の意味はホバーで `_HEADER_TOOLTIPS` から表示する。
        headings = {
            'name':    ('Package',          220),
            'current': ('Current',           80),
            'age_cur': ('Cur age',           55),
            'wanted':  ('Wanted',            90),
            'age_wnt': ('Wnt age',           55),
            'minor':   ('Minor up',          90),
            'age_min': ('Min age',           55),
            'major':   ('Major up',          90),
            'age_maj': ('Maj age',           55),
            'status':  ('Status',            70),
            'dev':     ('dev',               40),
            'prov':    ('prov',              50),
            'dep':     ('dep',               50),
            'license': ('License',           90),
            'gz':      ('Bundle (gz)',       80),
        }
        self._headings = headings
        right_aligned = {'age_cur', 'age_wnt', 'age_min', 'age_maj', 'gz'}
        centered = {'dev', 'prov', 'status', 'dep'}
        # ヘッダの色チップ画像を生成 (Wanted/Minor up/Major up 列のみ)。
        # PhotoImage は参照を失うと黒化するため self に保持。
        self._heading_swatches: dict[str, tk.PhotoImage] = {}
        for col_name, color in _HEADING_SWATCH_COLORS.items():
            img = tk.PhotoImage(master=tree, width=_HEADING_SWATCH_SIZE, height=_HEADING_SWATCH_SIZE)
            img.put(color, to=(0, 0, _HEADING_SWATCH_SIZE, _HEADING_SWATCH_SIZE))
            self._heading_swatches[col_name] = img
        for col, (label, width) in headings.items():
            swatch = self._heading_swatches.get(col)
            if swatch is not None:
                # tree.heading は -compound オプション非対応。image を渡すと
                # _ensure_heading_image_on_left() の layout 上書きで text の左に並ぶ。
                tree.heading(col, text=label, image=swatch)
            else:
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
        # 横 scrollbar: 'All' プリセットなど全列幅 > 表示幅で右端が切れるため必要。
        # 'name' 列以外は stretch=False なので、合計幅が widget より広いと自動で
        # スクロール可能になる。
        hsb = ttk.Scrollbar(self, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        tree.bind('<<TreeviewSelect>>', self._on_select)
        # Wanted カラム '?' セルにホバーツールチップ (raw spec 表示)
        tree.bind('<Motion>', self._on_motion)
        tree.bind('<Leave>', self._on_leave_tree)
        self.tree = tree
        # 行 iid → package dict のマップ（選択行の完全データ取得用）
        self._row_data: dict[str, dict] = {}
        # フィルタ前の完全データ。set_filter で再描画する際に使う
        self._all_packages: list[dict] = []
        self._filter_query = ''
        self._filter_status: str | None = None  # None=全件、'outdated' は major/minor/both 集約
        self._filter_dev_only = False
        # セルツールチップの現在表示状態
        self._cell_tip: tk.Toplevel | None = None
        self._cell_tip_key: tuple[str, str] | None = None
        # 初期プリセット (App から set_view_preset で上書きされる想定)
        self.set_view_preset(DEFAULT_PRESET)

    def set_view_preset(self, name: str) -> None:
        """表示列セットを切替える。COLUMNS は維持し displaycolumns だけ変える。"""
        cols = self.presets.get(name)
        if cols is None:
            # 不明な preset 名のフォールバック: 自テーブルの最初の preset を採用。
            cols = next(iter(self.presets.values()), self.COLUMNS)
        self.tree.configure(displaycolumns=cols)

    @staticmethod
    def _age_text(days) -> str:
        if days is None or days == '':
            return ''
        return f'{days}d'

    @staticmethod
    def _size_text(b) -> str:
        if not isinstance(b, (int, float)):
            return ''
        if b < 1024:
            return f'{int(b)} B'
        if b < 100 * 1024:
            return f'{b / 1024:.1f} KB'
        if b < 1024 * 1024:
            return f'{b / 1024:.0f} KB'
        return f'{b / 1024 / 1024:.1f} MB'

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
            wanted = p.get('allowedLatest')
            current = p.get('current')
            # Wanted セルの表示ルール:
            #   '?'        → 解釈不能 (そのまま表示; hover で raw spec ツールチップ)
            #   == current → 空欄 (上げ余地なし)
            #   それ以外    → version 文字列
            if wanted == UNPARSEABLE_SPEC_MARKER:
                wanted_display = UNPARSEABLE_SPEC_MARKER
                wanted_age = ''
            elif wanted and wanted != current:
                wanted_display = wanted
                wanted_age = self._age_text(p.get('allowedLatestAgeInDays'))
            else:
                wanted_display = ''
                wanted_age = ''
            iid = self.tree.insert('', 'end', values=(
                p.get('name', ''),
                current or '-',
                self._age_text(p.get('currentAgeInDays')),
                wanted_display,
                wanted_age,
                p.get('latestMinor') or '',
                self._age_text(p.get('latestMinorAgeInDays')),
                p.get('latestMajor') or '',
                self._age_text(p.get('latestMajorAgeInDays')),
                _STATUS_LABEL.get(p.get('status', ''), p.get('status', '')),
                'yes' if p.get('dev') else '',
                'yes' if p.get('provenance') else ('no' if p.get('provenance') is False else ''),
                self._dep_text(p),
                p.get('license') or '',
                self._size_text(p.get('gzip')),
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

    def get_selected_all(self) -> list[dict]:
        """選択中の全行を画面順で返す。"""
        return [self._row_data[iid] for iid in self.tree.selection() if iid in self._row_data]

    def _on_select(self, _event) -> None:
        if not self.on_select:
            return
        pkg = self.get_selected()
        if pkg:
            self.on_select(pkg)

    # ── Wanted セル '?' のホバーツールチップ ─────────────────────────
    def _resolve_column_name(self, col_id: str) -> str | None:
        """`#1` 形式の column id を 'name'/'wanted' 等の論理名に変換。

        Treeview の displaycolumns 設定を考慮する。
        """
        if not col_id or not col_id.startswith('#'):
            return None
        try:
            idx = int(col_id[1:]) - 1
        except ValueError:
            return None
        if idx < 0:
            return None
        cols = self.tree.cget('displaycolumns')
        if not isinstance(cols, tuple) or cols == ('#all',) or not cols:
            cols = self.COLUMNS
        if idx >= len(cols):
            return None
        return cols[idx]

    def _on_motion(self, event) -> None:
        col_id = self.tree.identify_column(event.x)
        # ヘッダ上 → 日本語の列説明を表示
        try:
            region = self.tree.identify_region(event.x, event.y)
        except tk.TclError:
            region = ''
        if region == 'heading' and col_id:
            self._show_header_tip(event, col_id)
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id or not col_id:
            self._hide_cell_tip()
            return
        col_name = self._resolve_column_name(col_id)
        if col_name != 'wanted':
            self._hide_cell_tip()
            return
        try:
            value = self.tree.set(row_id, 'wanted')
        except tk.TclError:
            self._hide_cell_tip()
            return
        if value != UNPARSEABLE_SPEC_MARKER:
            self._hide_cell_tip()
            return
        key = (row_id, col_name)
        if key == self._cell_tip_key:
            return  # 同じセルに留まっている間は再描画しない (チラつき防止)
        self._hide_cell_tip()
        pkg = self._row_data.get(row_id) or {}
        spec = pkg.get('spec') or '(unknown)'
        text = (
            f'spec を解釈できませんでした:\n'
            f'  {spec}\n\n'
            f'対応している記法: ^ ~ >= <= > < = (space で AND)、1.x / 1.2.x。\n'
            f'未対応の例: 1.0 || 2.0 / 1.0 - 2.0 / file: / git+ / workspace: / npm: 等。\n'
            f'手動で確認してください。'
        )
        bbox = self.tree.bbox(row_id, 'wanted')
        if bbox:
            x, y, _w, h = bbox
            rx = self.tree.winfo_rootx() + x
            ry = self.tree.winfo_rooty() + y + h + 2
        else:
            rx = self.tree.winfo_pointerx() + 12
            ry = self.tree.winfo_pointery() + 12
        try:
            tw = ui_tooltip.make_bubble(self.tree, text)
            tw.wm_geometry(f'+{rx}+{ry}')
            self._cell_tip = tw
            self._cell_tip_key = key
        except tk.TclError:
            self._cell_tip = None
            self._cell_tip_key = None

    def _show_header_tip(self, event, col_id: str) -> None:
        col_name = self._resolve_column_name(col_id)
        text = _HEADER_TOOLTIPS.get(col_name or '')
        if not text:
            self._hide_cell_tip()
            return
        key = ('__header__', col_name)
        if key == self._cell_tip_key:
            return
        self._hide_cell_tip()
        rx = self.tree.winfo_pointerx() + 14
        ry = self.tree.winfo_pointery() + 18
        try:
            tw = ui_tooltip.make_bubble(self.tree, text)
            tw.wm_geometry(f'+{rx}+{ry}')
            self._cell_tip = tw
            self._cell_tip_key = key
        except tk.TclError:
            self._cell_tip = None
            self._cell_tip_key = None

    def _on_leave_tree(self, _event) -> None:
        self._hide_cell_tip()

    def _hide_cell_tip(self) -> None:
        if self._cell_tip is not None:
            try:
                self._cell_tip.destroy()
            except tk.TclError:
                pass
        self._cell_tip = None
        self._cell_tip_key = None
