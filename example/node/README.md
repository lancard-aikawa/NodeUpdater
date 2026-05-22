# NodeUpdater dogfood fixture (node)

ロックファイルを **意図的に生成しない** 状態で各機能の境界ケースを確認するための
パッケージ。`npm install` を走らせると lockfile が出来てしまうので、検証目的では
そのままにしておくこと（commit するべきではないが、誤って走らせた場合は
`package-lock.json` / `node_modules/` を削除）。

## 何を試すための fixture か

- `express: ^4.18.0` — caret range (同 major 内の最新を許容)
- `lodash: ~4.17.20` — tilde range (同 minor 内の最新を許容)
- `chalk: 5.x` — partial version (5系の最新)
- `debug: *` — wildcard (制約なし)
- `left-pad: 1.3.0` — 固定版
- `@types/node: ^20.0.0` — scoped + caret
- `typescript: ^5.0.0` — devDependencies
- `eslint: latest` — `latest` tag (クールダウンインストールでは cooldown 適用後の latest になる)

## クールダウンインストールの確認手順

1. NodeUpdater を起動し、**Global タブ** で `クールダウンインストール…` を開く
2. Package 欄に `lodash` 等を入力 → `Resolve`
3. Cooldown が利いた版 (例: 7 日以上前の最新) が表示されることを確認
4. `npm latest` 行に **元の dist-tags.latest** が並び、cutoff より新しければ
   除外理由が表示される
5. `Install (new console)` でグローバルインストール (確認用なので
   不要になったら `npm uninstall -g <name>` で消す)

## Project タブの確認

このディレクトリを Project に選んだ後、`Refresh` で
`package.json` の spec が拾われ、Wanted / Latest 列に上記の挙動が出る。
