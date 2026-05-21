# PypkgUpdater dogfood fixture (py)

`.venv` を **意図的に作らない** 状態で各機能の境界ケースを確認するためのプロジェクト。
`uv sync` / `pip install -e .` を走らせると `.venv` / site-packages が出来て
未導入検出が消えてしまうので、検証目的ではそのままにしておくこと
(誤って作った場合は `.venv` / `*.egg-info` を削除)。

## 何を試すための fixture か

### dependencies (PEP 621)
- `requests>=2.31,<3.0` — operator chain (`,` で AND)
- `click~=8.1` — compatible release (`~=` ≒ `>=8.1,<9.0`)
- `rich==13.7.1` — exact pin (Wanted 列は空欄になるはず)
- `httpx>=0.27` — 上限なし (Wanted = 絶対最新 / cooldown 適用後の最新)
- `packaging` — spec 無し
- `PyYAML==6.0.*` — prefix wildcard

### optional-dependencies
- `dev`: pytest / mypy → dev=true で表示
- `docs`: mkdocs → dev=false, group='docs'

### dependency-groups (PEP 735)
- `lint`: ruff → dev=true (heuristic で lint をdev扱い)

## Safe Install の確認手順

1. PypkgUpdater を起動 → **Global タブ** で `安全インストール…`
2. Package 欄に `requests` 等を入力 → `Resolve`
3. cooldown 経過済みの版が表示されることを確認
4. PyPI latest 行に **元の info.version** が並び、cutoff より新しければ
   除外理由が表示される
5. `Install (new console)` で `pip install -U requests==X.Y.Z` が走る

## Project の確認

このディレクトリを Project に選んで `更新` すると、
8 件すべて青背景 + Status=`未導入` + Current=`-` になる。
状態フィルタを `未導入` にすれば未 install のみ表示。

`uv sync` を走らせて `.venv` を作ると、Current 列に実バージョンが入る。
