# PkgUpdater

Windows 向けの **パッケージ依存チェック・更新ツール群**。Python + tkinter 製で、サーバーやポートを使わずローカル GUI として動く。同じリポジトリに 2 つの独立 GUI が同居している:

- **NodeUpdater** — npm / yarn / pnpm / bun の依存を扱う (旧名 NodeUpdater 単体時代の機能)
- **PypkgUpdater** — pip / uv / Poetry の依存を扱う (PyPI 版)

両者は `shared/` 配下のキャッシュ・OSV クライアント・state 永続化を共有する。

> リポジトリ名はまだ `NodeUpdater` のままだが、内容はモノレポ化済み。GitHub 側で rename した時点でこの README の git URL も更新する。

## NodeUpdater (npm 系)

### できること

- **Project タブ**: `package.json` を読み、`registry.npmjs.org` から最新版・公開日・provenance を取得して latest / minor / major に色分け表示。workspaces 対応。
- **Global タブ**: `npm list -g --depth=0 --json` の結果に同じチェックをかける。選択して `npm install -g <name>@latest` も実行可。
- **Tree タブ**: `package-lock.json` から推移依存ツリーを表示。脆弱性ありノードをハイライト。
- **Audit タブ**: OSV.dev (ecosystem=`npm`) + `npm audit --json` の二系統。

### 起動

```cmd
:: 開発時 (リポジトリルートから)
py node\main.py                       # Global タブ初期表示
py node\main.py C:\path\to\project    # 指定プロジェクトを開く

:: ビルド後
NodeUpdater.exe
NodeUpdater.exe C:\path\to\project
```

## PypkgUpdater (PyPI 系)

### できること

- **Project タブ**: `pyproject.toml` ([PEP 621] / [PEP 735 dependency-groups] / Poetry) と `requirements*.txt` から直接依存を読み、`pypi.org` JSON API で最新版・公開日・license・yanked を取得。
- **Global タブ**: `python -m pip list --format=json` の結果に同じチェックをかける。
- **Audit タブ**: OSV.dev (ecosystem=`PyPI`) で直接依存をスキャン。lock 対応 (uv.lock / poetry.lock の推移依存) は今後。

[PEP 621]: https://peps.python.org/pep-0621/
[PEP 735]: https://peps.python.org/pep-0735/

### 起動

```cmd
:: 開発時 (リポジトリルートから)
py py\main.py                         # Global タブ初期表示
py py\main.py C:\path\to\project      # 指定プロジェクトを開く

:: ビルド後
PypkgUpdater.exe
PypkgUpdater.exe C:\path\to\project
```

エクスプローラの右クリック「送る」メニューにショートカットを置けば、フォルダを右クリック → 各 Updater で開ける。

## ビルド (PyInstaller)

```cmd
py -m pip install --user pyinstaller

build.cmd                 :: 両方ビルド → dist\NodeUpdater.exe + dist\PypkgUpdater.exe
node\build.cmd            :: NodeUpdater のみ
py\build.cmd              :: PypkgUpdater のみ
```

## キャッシュ・state

- 第一候補: `<exe フォルダ>\cache\` (dev ではリポジトリルート)
- 書き込み不可なら: `%LOCALAPPDATA%\PkgUpdater\cache\` にフォールバック
- TTL は registry が 24h、OSV が 12h。`Force Refresh` で無視可能。
- 設定 (proxy / 並列数 / cooldown / GitHub token) は `state.json` を両 GUI で共有。npm registry URL と PyPI index URL は別キー。

## ディレクトリ構成

```
PkgUpdater/                ← リポジトリ
  shared/                  ← 両 GUI 共通
    cache.py               — TTL 付き JSON キャッシュ
    state.json/state.py    — 設定・最近開いたプロジェクト
    osv.py                 — OSV.dev クライアント (ecosystem 引数で切替)
    github_releases.py     — Changelog 用
    history.py             — Install 試行ログ
    audit_export.py        — OSV / npm audit を md/csv に書き出し
  node/                    ← NodeUpdater 専用
    main.py                — エントリ
    build.cmd              — PyInstaller ビルド
    core/
      semver.py, npm_registry.py, npm_global.py,
      package_json.py, package_lock.py, bun_lock.py,
      bundlephobia.py, pkg_manager.py
    ui/
      app.py, table.py,
      changelog_dialog.py, history_dialog.py,
      install_dialog.py, settings_dialog.py
  py/                      ← PypkgUpdater 専用
    main.py                — エントリ
    build.cmd
    core/
      pep440.py            — PEP 440 パース・比較
      pypi.py              — pypi.org JSON API クライアント
      pyproject.py         — pyproject.toml / requirements.txt 読み取り
      pip_global.py        — `pip list --format=json` 実行
    ui/
      app.py, table.py
  build.cmd                — 両方ビルド
```

## 依存

- Python 3.11+ (PypkgUpdater 側で `tomllib` を使うため。NodeUpdater 単体なら 3.10+)
- 標準ライブラリのみ (`requirements.txt` 不要)。`pyinstaller` はビルド時のみ。
- NodeUpdater のグローバル列挙・更新には `npm` (および `bun` / `pnpm` / `yarn` がある場合はそれら) が PATH に必要。
- PypkgUpdater のグローバル列挙には `pip` (= `python -m pip`) が PATH の Python に必要。
