# 機能追加候補 (Release Candidates)

NodeUpdater の今後追加していく可能性のある機能候補をまとめたメモ。
着手するかどうかは個別に判断する。

## カテゴリ別候補

### 1. 運用品質の底上げ

#### 1-A. OSV 結果のキャッシュ [実装済み]
- **概要**: OSV スキャン結果をプロジェクト単位でキャッシュ (12h)。`package-lock.json` (無ければ `package.json`) の mtime と比較して、lock が更新されていれば失効扱い。
- **狙い**: 大規模 lock (数百〜数千件) の再スキャンを体感数十倍速にする。CI/オフライン環境の利便性向上。
- **既存参考実装**: FlutterBoard `server/osvCheck.js` の `loadOsvCache` / `saveOsvCache`。
- **実装規模**: 小 (キャッシュ I/O 追加のみ)。`core/cache.py` が既に存在するので流用可能。
- **実装**: `core/cache.py` の `load()` に `invalidate_if_newer: Path` 引数を追加して mtime 失効に汎用対応。Audit タブに `Force Rescan` ボタンを追加。

#### 1-B. 依存ツリー可視化 (Treeview) [実装済み]
- **概要**: `package-lock.json` の `packages` から階層構造を再構築し、`ttk.Treeview` で `npm ls` 相当の表示。
- **狙い**: 推移依存の出所を視覚的に把握 (現状は文字列で「← foo, bar」)。脆弱性のあるノードを赤色等で強調。
- **実装規模**: 中。`core/package_lock.py` にツリー構築関数を追加し、UI に新タブまたは Audit タブの展開ビューを追加。
- **実装**: Tree タブを新規追加。`package_lock.build_tree()` で物理 node_modules 階層を再構築 (スコープ付き / ネスト両対応)。OSV キャッシュがあれば該当ノードを赤背景 + `vuln×N` フラグで強調。Expand / Collapse all ボタンと件数表示も追加。

#### 1-D. Cooldown (公開待機) フィルタ
- **概要**: 「公開から N 日経過していない版」を最新候補から除外する設定。`dist-tags.latest` がカットオフより新しい場合は、カットオフ前の最新安定版を実効的な latest として扱う。設定は Spinbox で GUI 上から変更可能、`state.json` に永続化。
- **狙い**: npm パッケージ乗っ取り (typosquatting / 一時的な悪意ある公開) に対する時間バッファ。ユーザーのグローバル設定 (uv `exclude-newer = "P7D"` / pip `uploaded-prior-to = P7D`) と方針を揃える。
- **挙動**: 脆弱性スキャン (OSV / npm audit) には影響させない (脆弱性は年齢に関わらず報告)。更新候補の選定 (`latestMinor` / `latestMajor`) のみに適用。
- **実装規模**: 中。`npm_registry.fetch_one` が既に `time_map` を取得しているため、カットオフ判定で版集合をフィルタするだけで成立。キャッシュキーに cooldown 値を含めて整合性を保つ。

#### 1-C. 検索 / フィルタ [実装済み]
- **概要**: Project / Global テーブルで名前・状態 (major 更新あり、脆弱性あり、dev のみ 等) で絞り込む検索ボックス。
- **狙い**: パッケージ数が増えた際の UX 改善。現状は全件スクロールしか手段がない。
- **実装規模**: 小。`ui/table.py` にフィルタ層を追加。
- **実装**: 各タブに Filter (名前部分一致) / Status コンボ (All/Outdated/Major/Minor/Both/Latest/Unknown) / Dev only チェック / 表示件数 (visible / total) を追加。`PackageTable` にフィルタ状態と `on_render` コールバックを実装。

### 2. 判断材料の充実

#### 2-A. changelog / release notes 表示
- **概要**: 選択行に対して current → latest の差分の release notes を表示。GitHub Releases API または `npm view <pkg> versions` + repository から取得。
- **狙い**: 特に major update 時の Breaking Change 確認の手間を減らす。
- **実装規模**: 中。GitHub API レート制限の考慮が必要 (未認証で 60 req/h)。

#### 2-B. deprecation / ライセンス表示 [実装済み]
- **概要**: npm registry のメタデータから `deprecated` フラグと `license` フィールドを取得し、Project / Global テーブルに列追加。
- **狙い**: deprecated パッケージの早期発見 (移行候補抽出)、GPL 系等の法務確認用途。
- **実装規模**: 小。`core/npm_registry.py` の `fetch_many` が既に registry にアクセスしているので、取得フィールド追加のみ。
- **実装**: `dep` 列 (`yes` / `abnd` / 空) と `License` 列を追加。選択行が deprecated ならステータスバーに registry の deprecation message を表示。

#### 2-C. package size 表示
- **概要**: bundlephobia API で minified / install size を取得して列表示。
- **狙い**: フロントエンド向けに bundle 影響を把握。
- **実装規模**: 小〜中。bundlephobia API へのアクセス追加。失敗時の挙動 (静かに空表示) を含む。

### 3. スコープ拡張

#### 3-A. pnpm / yarn lockfile 対応
- **概要**: `pnpm-lock.yaml` / `yarn.lock` のパースを追加し、OSV スキャンの対象にする。
- **狙い**: 社内で混在する場合の対応。
- **実装規模**: 中。lockfile フォーマットがそれぞれ異なるためパーサ実装が必要。

#### 3-B. monorepo / workspaces 対応
- **概要**: `package.json` の `workspaces` フィールドを検出し、サブプロジェクト単位でタブまたはセレクタを表示。
- **狙い**: yarn workspaces / npm workspaces 構成のプロジェクトでも単独利用可能に。
- **実装規模**: 中。`core/package_json.list_subprojects()` の延長として実装可能。

#### 3-C. lockfile v1 サポート
- **概要**: 現在 v2/v3 のみ対応。v1 (npm v6 以前) の `dependencies` ツリー形式もパース。
- **狙い**: 古いプロジェクトへの後方互換性。
- **実装規模**: 小。需要があるかによる。

### 4. ワークフロー改善

#### 4-A. 複数選択での一括更新 [実装済み]
- **概要**: テーブルで複数行選択し、一括で minor up / major up を実行 (順次プロンプト起動 or 1 つのプロンプトで `npm install a@x b@y c@z`)。
- **狙い**: 5〜10 パッケージをまとめて更新する際の手数削減。
- **実装規模**: 小。`PackageTable.get_selected()` を複数対応に拡張、`_install_selected` をループ化。
- **実装**: Treeview を `selectmode='extended'` に変更し `get_selected_all()` を追加。1 つの `npm install a@x b@y c@z` にまとめて起動。対象更新が無いパッケージはスキップし、確認ダイアログにスキップ件数も表示。

#### 4-B. 監査レポートのエクスポート
- **概要**: OSV / npm audit の結果を Markdown または CSV で出力。
- **狙い**: PR / Issue / 報告書への貼り付け、定点観測。
- **実装規模**: 小。

#### 4-C. 設定ダイアログ
- **概要**: registry URL、proxy、キャッシュ TTL を GUI から変更可能に。
- **狙い**: 社内 registry / 認証付き proxy 環境への対応。
- **実装規模**: 中。`state.json` に設定セクションを追加。

## 優先度の所感

費用対効果が高い順 (現時点での主観):

1. ~~**1-D. Cooldown フィルタ**~~ — 実装済み
2. ~~**2-B. deprecation / ライセンス表示**~~ — 実装済み
3. ~~**1-A. OSV 結果のキャッシュ**~~ — 実装済み
4. ~~**1-C. 検索 / フィルタ**~~ — 実装済み
5. ~~**4-A. 複数選択での一括更新**~~ — 実装済み
6. ~~**1-B. 依存ツリー可視化**~~ — 実装済み

優先度は要件・利用者層によって変わるため、議論の上で決める。
