"""NodeUpdater エントリポイント。

使い方:
  NodeUpdater.exe                  # Global タブ初期表示
  NodeUpdater.exe <project-path>   # 指定プロジェクトを開く
  NodeUpdater.exe .                # カレントディレクトリを開く
"""
from __future__ import annotations

import sys
from pathlib import Path

# スクリプト直起動 (py node/main.py) と PyInstaller 実行両方で
# `node` / `shared` パッケージを解決できるようにリポジトリルートを sys.path に追加。
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from node.ui.app import App


def _resolve_project(argv: list[str]) -> Path | None:
    if len(argv) < 2:
        return None
    p = Path(argv[1]).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        print(f'NodeUpdater: not a directory: {p}', file=sys.stderr)
        return None
    return p


def main() -> int:
    project = _resolve_project(sys.argv)
    app = App(initial_project=project)
    app.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
