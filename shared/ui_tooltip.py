"""簡易ツールチップ。tkinter 標準ライブラリのみで実装。

使い方:
    from shared import ui_tooltip
    ui_tooltip.attach(some_button, '説明テキスト')

複数行・長文も `wraplength` で自動折返し。click や leave で即座に消える。
NodeUpdater / PypkgUpdater 両方から使う共通 UI ヘルパー。
"""
from __future__ import annotations

import tkinter as tk


class Tooltip:
    """1 ウィジェットに対するツールチップ。hover で `delay_ms` 後に表示。"""

    def __init__(
        self,
        widget: tk.Misc,
        text: str,
        delay_ms: int = 500,
        wraplength: int = 360,
    ):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._tipwindow: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind('<Enter>', self._on_enter, add='+')
        widget.bind('<Leave>', self._on_leave, add='+')
        # ボタン押下時にも消す (押した後にチップが残るのは不快)
        widget.bind('<ButtonPress>', self._on_leave, add='+')
        # ウィジェット破棄時にも残らないように
        widget.bind('<Destroy>', self._on_leave, add='+')

    def update_text(self, text: str) -> None:
        """表示中ならその場で書き換える (radio などで動的に説明を変えたい時用)。"""
        self.text = text
        if self._tipwindow:
            self._hide()
            self._show()

    def _on_enter(self, _event=None) -> None:
        self._cancel_pending()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event=None) -> None:
        self._cancel_pending()
        self._hide()

    def _show(self) -> None:
        if self._tipwindow is not None:
            return
        try:
            # ウィジェットの直下に出す。マウス位置に追従しないシンプル版。
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            return  # ウィジェットが既に破棄されているケース
        tw = make_bubble(self.widget, self.text, wraplength=self.wraplength)
        tw.wm_geometry(f'+{x}+{y}')
        self._tipwindow = tw

    def _hide(self) -> None:
        if self._tipwindow is not None:
            try:
                self._tipwindow.destroy()
            except tk.TclError:
                pass
            self._tipwindow = None

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None


def attach(
    widget: tk.Misc,
    text: str,
    delay_ms: int = 500,
    wraplength: int = 360,
) -> Tooltip:
    """ショートカット: Tooltip(widget, text) のインスタンスを返す。"""
    return Tooltip(widget, text, delay_ms=delay_ms, wraplength=wraplength)


def make_bubble(parent: tk.Misc, text: str, wraplength: int = 360) -> tk.Toplevel:
    """装飾なしの黄色いバブル Toplevel を作って返す (位置・破棄は呼び出し側責任)。

    Tooltip クラスは widget 1 個 + hover delay 向け。Treeview のセルみたいに
    「位置が動的に変わるバブル」が欲しい時はこの low-level 関数を直接使う。
    """
    tw = tk.Toplevel(parent)
    tw.wm_overrideredirect(True)
    tk.Label(
        tw,
        text=text,
        justify='left',
        background='#ffffe0',
        foreground='#000000',
        relief='solid',
        borderwidth=1,
        font=('TkDefaultFont', 9),
        padx=6, pady=4,
        wraplength=wraplength,
    ).pack()
    return tw
