# NodeUpdater

Windows 向けの npm 依存チェック・更新ツール。Python + tkinter 製で、サーバーやポートを使わずローカル GUI として動く。FlutterBoard の依存チェック機能を独立させたもの。

## できること

- **Project タブ**: 指定フォルダの `package.json` を読み、`registry.npmjs.org` から最新版・公開日・provenance を取得して latest / minor / major に色分け表示
- **Global タブ**: `npm list -g --depth=0 --json` の結果に同じチェックをかける。選択して `npm install -g <name>@latest` も実行可
- **Audit タブ**: OSV.dev に問い合わせて npm エコシステムの既知脆弱性を一覧

## 起動方法

```cmd
:: 開発時
py main.py                       # Global タブ初期表示
py main.py C:\path\to\project    # 指定プロジェクトを開く
py main.py .                     # カレントディレクトリを開く

:: ビルド後
NodeUpdater.exe
NodeUpdater.exe C:\path\to\project
```

エクスプローラの右クリック「送る」メニューにショートカットを置けば、フォルダを右クリック → NodeUpdater で開ける。

## ビルド (PyInstaller で単一 exe 化)

```cmd
py -m pip install --user pyinstaller
build.cmd
```

`dist\NodeUpdater.exe` が生成される。これをどこにでも配置可能。

## キャッシュ

- 第一候補: `<exe フォルダ>\cache\`
- 書き込み不可なら: `%LOCALAPPDATA%\NodeUpdater\cache\` にフォールバック
- TTL は 24 時間。`Force Refresh` ボタンで無視可能

## ディレクトリ構成

```
NodeUpdater/
  main.py              — エントリポイント
  ui/
    app.py             — Tk root と各タブ
    table.py           — Treeview ベースの依存一覧
  core/
    cache.py           — TTL 付き JSON キャッシュ
    semver.py          — semver パース・比較
    npm_registry.py    — registry.npmjs.org クライアント
    npm_global.py      — npm CLI 経由のグローバル列挙・更新
    package_json.py    — package.json 読み書き
    osv.py             — OSV.dev クライアント
  build.cmd            — PyInstaller ビルドスクリプト
```

## 依存

- Python 3.10+ (標準ライブラリのみ。`requirements.txt` 不要)
- グローバル列挙と更新には `npm` コマンドが PATH に必要
