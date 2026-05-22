"""クールダウンインストールダイアログ: 単一パッケージを cooldown 適用後の版で導入する。

`InstallDialog` は「既存テーブルから選んだ複数 spec を一括プレビュー」用だが、
こちらは「未インストールのパッケージを 1 つずつ、cooldown 経過済みの版で安全に
入れる」用途 (Global タブが主用途、Project の単発追加でも使える)。

フロー:
  1. Package 名 (+ 任意の version constraint) を入力
  2. Resolve ボタン → registry に問い合わせて cooldown 適用後の install 候補を提示
  3. OK なら Install ボタンで新規 console を起動
  4. 続けて別パッケージを入れたい場合は入力欄を消して再入力 (ダイアログは閉じない)
  5. Close 時、1 回以上 Install を打っていれば on_installed コールバックを発火し
     呼び出し側 (Global タブ) がテーブルを refresh する

ecosystem 中立: resolver / pkg_manager / opener / global_install を構築時に渡す。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable


class SafeInstallDialog(tk.Toplevel):
    def __init__(
        self,
        master,
        *,
        title: str,
        pm: str,
        global_install: bool,
        cwd: str | None,
        cooldown_days: int,
        resolver: Callable[[str, str | None, int], dict],
        pkg_manager: Any,
        opener: Callable[[str, str | None], None],
        on_installed: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.geometry('760x540')

        self.pm = pm
        self.global_install = global_install
        self.cwd = cwd
        self.cooldown_days = cooldown_days
        self.resolver = resolver
        self.pkg_manager = pkg_manager
        self.opener = opener
        self.on_installed = on_installed

        # 直近の resolve 結果 (Install 押下時に使う)
        self._last_resolved: dict | None = None
        # Install を 1 回でも打ったか (Close 時の refresh 判定用)
        self._installed_any = False

        body = ttk.Frame(self, padding=8)
        body.pack(fill='both', expand=True)

        scope_label = 'Global (-g)' if global_install else f'Project: {cwd or "(cwd)"}'
        ttk.Label(
            body,
            text=f'クールダウンインストール  [{pm}]  {scope_label}  /  cooldown={cooldown_days}日',
            font=('TkDefaultFont', 10, 'bold'),
        ).pack(anchor='w')

        # 入力行
        form = ttk.Frame(body)
        form.pack(fill='x', pady=(8, 4))
        ttk.Label(form, text='Package:').grid(row=0, column=0, sticky='w')
        self.name_var = tk.StringVar()
        name_entry = ttk.Entry(form, textvariable=self.name_var, width=42)
        name_entry.grid(row=0, column=1, sticky='w', padx=(6, 0))
        ttk.Label(form, text='Constraint:', foreground='#666').grid(
            row=0, column=2, sticky='w', padx=(12, 0))
        self.spec_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.spec_var, width=18).grid(
            row=0, column=3, sticky='w', padx=(6, 0))
        ttk.Label(
            form, text='(任意: ^1.2 / ~1.2.3 / 1.2.3 / 空欄=最新)',
            foreground='#888',
        ).grid(row=1, column=1, columnspan=3, sticky='w', padx=(6, 0), pady=(2, 0))

        # Resolve / Install ボタン
        btn_row = ttk.Frame(body)
        btn_row.pack(fill='x', pady=(8, 4))
        self.resolve_btn = ttk.Button(btn_row, text='Resolve', command=self._on_resolve)
        self.resolve_btn.pack(side='left')
        self.install_btn = ttk.Button(
            btn_row, text='Install (new console)', command=self._on_install, state='disabled',
        )
        self.install_btn.pack(side='left', padx=(6, 0))
        ttk.Button(btn_row, text='Clear', command=self._clear_inputs).pack(side='left', padx=(6, 0))
        ttk.Button(btn_row, text='Close', command=self._on_close).pack(side='right')

        ttk.Separator(body, orient='horizontal').pack(fill='x', pady=(6, 4))

        ttk.Label(body, text='Resolution:', foreground='#666').pack(anchor='w')
        text_frame = ttk.Frame(body)
        text_frame.pack(fill='both', expand=True, pady=(2, 0))
        self.text = tk.Text(
            text_frame, wrap='word', height=14, state='disabled',
            font=('TkFixedFont', 9),
        )
        self.text.pack(side='left', fill='both', expand=True)
        vsb = ttk.Scrollbar(text_frame, orient='vertical', command=self.text.yview)
        vsb.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=vsb.set)
        # 強調用タグ
        self.text.tag_configure('ok', foreground='#0a6')
        self.text.tag_configure('warn', foreground='#c60')
        self.text.tag_configure('err', foreground='#c00')
        self.text.tag_configure('dim', foreground='#888')
        self.text.tag_configure('cmd', font=('TkFixedFont', 9, 'bold'))
        self._set_text([('(Package 名を入力して Resolve を押してください)', 'dim')])

        # status バー (ダイアログ内)
        self.status_var = tk.StringVar(value='')
        ttk.Label(body, textvariable=self.status_var, foreground='#666').pack(anchor='w', pady=(4, 0))

        self.bind('<Escape>', lambda _e: self._on_close())
        self.bind('<Return>', lambda _e: self._on_resolve())
        name_entry.focus_set()
        self.after(50, self.grab_set)

        # Window 閉じるボタン (X) も自前ハンドラに繋いで refresh を確実に発火
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── 表示ヘルパ ──────────────────────────────────────────────────────────
    def _set_text(self, segments: list[tuple[str, str | None]]) -> None:
        """segments: [(text, tag or None), ...] を Text に流し込む。"""
        self.text.config(state='normal')
        self.text.delete('1.0', 'end')
        for txt, tag in segments:
            if tag:
                self.text.insert('end', txt, (tag,))
            else:
                self.text.insert('end', txt)
        self.text.config(state='disabled')

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_var.set(text)

    def _clear_inputs(self) -> None:
        self.name_var.set('')
        self.spec_var.set('')
        self._last_resolved = None
        self.install_btn.config(state='disabled')
        self._set_text([('(Package 名を入力して Resolve を押してください)', 'dim')])
        self._set_status('')

    # ── Resolve ─────────────────────────────────────────────────────────────
    def _on_resolve(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            self._set_status('Package 名を入力してください')
            return
        spec = self.spec_var.get().strip() or None
        self.resolve_btn.config(state='disabled')
        self.install_btn.config(state='disabled')
        self._last_resolved = None
        self._set_text([(f'Resolving {name}…', 'dim')])
        self._set_status(f'registry へ問い合わせ中: {name}')

        def work():
            try:
                result = self.resolver(name, spec, self.cooldown_days)
                self.after(0, lambda: self._show_resolution(result))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self.after(0, lambda: self._show_error(msg))

        threading.Thread(target=work, daemon=True).start()

    def _show_error(self, msg: str) -> None:
        self.resolve_btn.config(state='normal')
        self._set_text([('Error: ', 'err'), (msg, None)])
        self._set_status('resolve 失敗')

    def _show_resolution(self, r: dict) -> None:
        self.resolve_btn.config(state='normal')
        self._last_resolved = r
        name = r.get('name', '')

        if not r.get('found'):
            self._set_text([('Not found: ', 'err'), (r.get('reason') or name, None)])
            self._set_status('registry に存在しません')
            return

        resolved = r.get('resolved')
        segs: list[tuple[str, str | None]] = []

        if resolved:
            segs.append(('Resolved:    ', None))
            segs.append((f'{name}@{resolved}\n', 'ok'))
            pub = r.get('resolved_published_at')
            age = r.get('resolved_age_days')
            if pub:
                age_text = f' ({age}日前)' if age is not None else ''
                segs.append(('Published:   ', None))
                segs.append((f'{_fmt_date(pub)}{age_text}\n', None))
        else:
            segs.append(('Resolve 不可: ', 'err'))
            segs.append(((r.get('reason') or '理由不明') + '\n', None))

        raw_latest = r.get('raw_latest')
        if raw_latest and raw_latest != resolved:
            segs.append(('\nnpm latest: ', 'dim'))
            age = r.get('raw_latest_age_days')
            age_text = f' ({age}日前)' if age is not None else ''
            segs.append((f'{raw_latest}{age_text}', 'warn'))
            segs.append(('  ← cooldown で除外\n', 'dim'))

        excluded = r.get('excluded_newer') or []
        # raw_latest が excluded 先頭ならそれは上の行で示しているので 2 件目以降を示す
        rest = [e for e in excluded if e.get('version') != raw_latest]
        if rest:
            segs.append(('\nCutoff より新しい候補 (除外):\n', 'dim'))
            for e in rest[:4]:
                age = e.get('age_days')
                age_text = f'{age}日前' if age is not None else '?'
                segs.append((f'  • {e["version"]}  ({age_text})\n', 'dim'))

        if resolved:
            cmd = self.pkg_manager.install_command(
                self.pm, [f'{name}@{resolved}'], global_install=self.global_install,
            )
            segs.append(('\nInstall command:\n', 'dim'))
            segs.append((f'  {cmd}\n', 'cmd'))
            self.install_btn.config(state='normal')
            self._set_status('Install ボタンで新規 console を起動します')
        else:
            self.install_btn.config(state='disabled')
            self._set_status(r.get('reason') or 'resolve できませんでした')

        self._set_text(segs)

    # ── Install ─────────────────────────────────────────────────────────────
    def _on_install(self) -> None:
        r = self._last_resolved
        if not r or not r.get('resolved'):
            self._set_status('先に Resolve を実行してください')
            return
        name = r.get('name')
        version = r.get('resolved')
        cmd = self.pkg_manager.install_command(
            self.pm, [f'{name}@{version}'],
            global_install=self.global_install,
        )
        try:
            self.opener(cmd, self.cwd)
        except OSError as e:
            self._set_text([('Console 起動失敗: ', 'err'), (str(e), None)])
            self._set_status('console を起動できませんでした')
            return
        self._installed_any = True
        self._set_status(f'別 console で実行中: {cmd}  (完了後 Close で refresh)')
        # 続けて別パッケージを入れられるよう入力欄をクリア
        self._clear_inputs()

    # ── Close ───────────────────────────────────────────────────────────────
    def _on_close(self) -> None:
        cb = self.on_installed if self._installed_any else None
        self.destroy()
        if cb is not None:
            try:
                cb()
            except Exception:
                pass


def _fmt_date(iso_ts: str) -> str:
    """ISO timestamp の date 部分だけ返す表示用ヘルパ。"""
    if not iso_ts:
        return ''
    return iso_ts[:10]
