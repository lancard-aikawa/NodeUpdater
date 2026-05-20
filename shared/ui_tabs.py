"""ttk.Notebook の選択タブを判別しやすくする共通スタイル登録。

Windows の `vista` テーマ (tkinter 既定) では選択タブと非選択タブの
コントラストがほぼ無く、ユーザーが現在開いているタブを判別できない。
全プロジェクトで同じスタイル名を使い回すための冪等関数を提供する。

使用:
    notebook = ttk.Notebook(parent, style=ensure_notebook_style())

同じプロセスで複数回呼んでも副作用は無い (style.map を上書きするだけ)。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_DEFAULT_STYLE_NAME = 'App.TNotebook'


def ensure_notebook_style(name: str = _DEFAULT_STYLE_NAME) -> str:
    """指定名のスタイルを (冪等に) 登録し、その名前を返す。

    Notebook 側で `style=ensure_notebook_style()` のように使う。
    スタイルを受け付けないテーマでは黙って fallback する。
    """
    style = ttk.Style()
    try:
        style.configure(f'{name}.Tab', padding=[14, 6])
        style.map(
            f'{name}.Tab',
            background=[('selected', '#ffffff'), ('!selected', '#e8e8e8')],
            foreground=[('selected', '#000000'), ('!selected', '#757575')],
            font=[('selected', ('', 10, 'bold')), ('!selected', ('', 9))],
        )
    except tk.TclError:
        pass
    return name
