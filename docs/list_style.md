# 一覧表示 (PackageTable) のスタイリング検討

`ttk.Treeview` を使った Project / Global タブの一覧について、過去に検討した
スタイリング案と現在の到達点をまとめる。同種の改善案が将来出てきたときに
「過去に試したか」を辿れるようにするための備忘録。

## 解決済みの選択肢

### 行単位の status カラーリング [採用済み]
- `tree.tag_configure(status, background=...)` で行全体を着色。
- `_STATUS_COLORS`: `both`(赤) / `major`(橙) / `minor`(黄) / `latest`(緑) /
  `not_installed`(青) / `unknown`(灰)。
- 並び替えは `_STATUS_ORDER` で重大度順 (both → ... → latest)。
- 制約: ttk.Treeview は **行単位** にしかタグを適用できない。1 行内で列ごとに
  background / foreground を変える組み込み機能はない。

### 横スクロールバー [採用済み]
- `All` プリセットなど合計列幅 > widget 幅で右端列が見えなくなる対策。
- `name` 列以外 `stretch=False` 固定幅 → 合計が超過すると自動で xscroll 可能。

### 列ヘッダの色チップ画像 [採用済み: node / py 両方]
- セル単位 background が出せない代わりに、**ヘッダ左に小さい色チップ画像** を入れて
  「どのレーンか」を視覚識別する案。
- 仕組み: `tk.PhotoImage(width=10, height=10)` を `img.put(color, to=...)` で
  単色塗りつぶし → `tree.heading(col, image=img, compound='left')`。
- 配色:
  - Wanted = 緑 (`#5fbf5f`) … spec 内、安全度高
  - Minor up = 黄 (`#e6c440`) … 同 major 内
  - Major up = 橙 (`#e08a3c`) … Breaking 注意
- 利点: vista / clam / aqua すべてのテーマで動く。セルは素のまま (色潰れ無し)。
  外部画像ファイル不要 (コード内で生成)。
- 注意点:
  - `PhotoImage` は参照が消えると黒化するので `PackageTable` インスタンスに
    `_heading_swatches` として保持する。`master=tree` を渡して widget 単位で
    ライフサイクル管理。
  - `tree.heading()` は **`compound` オプション非対応** (column や Button 等
    とは違う API)。デフォルトの vista テーマでは image が text の **右側** に
    配置されるため、これだと「色チップが次の列の手前に出る」奇妙な見た目に
    なる。対策として `ttk.Style().layout('Treeview.Heading', ...)` で
    `image` element の `side='left'` を上書きする (本ファイル内の
    `_ensure_heading_image_on_left()`)。これは process global に効くので
    1 度呼べば全 Treeview に伝搬。
- 評価: py 側で視認性改善を確認後、node 側にも同じ実装を展開済み。

## ボツになった案

### A. 色付き emoji を cell 内 prefix する案 [ボツ]
- 試した内容: Wanted=🟢 / Minor up=🟡 / Major up=🟠 を version の前に挿入。
- ボツ理由: **Windows tkinter のデフォルトフォントが U+1F7E0〜E2 を持たない**
  ため、グリフ未定義のフォールバック (◎ のような単色丸) で描画されてしまい
  色味が完全に消える。font をフォールバック設定にしても、Tk widget 単位で
  指定する必要があり、列単位の font 切替もできないので根本解決にならない。
- 派生案: モノクロでも shape で区別する Unicode (▴ ▸ ⇈ 等) を使えば
  「色」は失うものの「どのレーンか」は伝わる。ただし「色分けが欲しい」
  という当初要求は満たさないので現状では未採用。

## 未着手 (将来検討)

### B. tksheet など Canvas ベース widget に置き換える
- 概要: `tksheet` (純 Python の独自テーブル widget) は **セル単位** で
  `background` / `foreground` / `font` を設定可能。CellEdit や Excel ライクな
  選択挙動も持つ。
- メリット: Wanted/Minor/Major の cell を直接 `set_cell_options(highlight=...)`
  で色付けできる。`td.wanted { background: #e8ffe8 }` 相当が実現可能。
- デメリット:
  - 外部依存追加 (純 Python だが LOC 規模はそこそこ)。
  - 現行 `PackageTable` の API (`set_packages` / `set_filter` /
    `set_view_preset` / on_select コールバック等) を tksheet API に書き直す
    必要がある。displaycolumns 相当の機能は別 API。
  - フォント / テーマが ttk と若干ずれる → app 全体の一貫性を意識する必要。
- 実装規模: 中〜大。`PackageTable` を tksheet 版に置き換える PoC を別 branch
  で試し、UX が改善するか先に評価したい。

### C. TkinterWeb (v4) で HTML/CSS テーブルにする
- 概要: Tkhtml3 を wrap した `HtmlFrame` widget で HTML/CSS を直接レンダリング。
  v4 系で JS/Form/SVG 周りが強化された。
- メリット: `<table>` + CSS でセル単位の色・グラデ・hover・条件付き書式が
  自由。デザイナーが触りやすい。
- デメリット:
  - Tkhtml3 のバイナリ依存 (Windows/macOS/Linux 別ホイール)。
  - 行選択 / 複数選択 / キーボード操作 / 列ソート / スクロール同期を HTML 側で
    再実装する必要がある (Treeview の `selectmode='extended'` 等はそのままでは
    効かない)。
  - 「テーブルだけ HTML、他は ttk」のハイブリッドは font / theme がズレやすい。
- 実装規模: 大。テーブル以外も含めて UI を HTML 化するなら投資する価値は
  あるが、テーブル 1 つのために導入するのはコスト過剰。

### D. Tcl/Tk 9.0 へのアップグレード
- Tk 9.0 (2024-10 リリース) の主な変更点: Unicode 15+ + 色 emoji ネイティブ
  対応 / HiDPI / SVG (`PhotoImage -format svg`) / テーマ刷新。
- **Treeview の per-cell color は Tk 9 でも追加されていない** (タグは依然
  row 単位)。なので「色分けしたい」という要求の解決にはならない。
- 色 emoji 対応で前述案 A が動く **可能性** はあるが、Tk 9 を使うには
  Python 3.14+ または手動 Tk バンドルが必要で、現状の Python 3.11/3.12
  環境とは互換性が崩れる。コスト > メリットなので保留。

### E. 列ヘッダの **背景** を変える (画像ではなく Style 経由)
- 概要: `ttk.Style().configure('Treeview.Heading', background='#...')` で
  ヘッダ背景を変える。
- ボツ理由:
  - Windows の `vista` テーマ (デフォルト) は heading を OS Common Controls
    が描画するため background の上書きが **無視される**。
  - `clam` / `alt` テーマに切り替えれば効くが、Windows ネイティブ感が崩れる
    + **全ヘッダ一律** にしか効かないので列ごとの差別化はできない。
- ヘッダ単位の色付けは「解決済みの選択肢」セクションの色チップ画像案で
  代替されている。

## 現在の到達点

- 行 = 全体重症度を `_STATUS_COLORS` で着色 (上記「解決済み」)。
- 列ヘッダ = npm/PyPI CLI 用語のまま、hover で JP 説明 (`_HEADER_TOOLTIPS`)。
- 列ヘッダ左に **色チップ画像** で Wanted/Minor/Major レーンを視覚識別
  (node / py 両方適用済み)。
- セル単位の色分けは保留。要件が再燃したら tksheet 移行を検討。

## 参考リンク

- ttk.Treeview style/tag リファレンス:
  https://tkdocs.com/tutorial/tree.html
- Tk 9.0 リリースノート:
  https://www.tcl.tk/software/tcltk/9.0.html
- tksheet:
  https://github.com/ragardner/tksheet
- TkinterWeb:
  https://github.com/Andereoo/TkinterWeb
