"""パッケージの GitHub Release notes を表示する Toplevel ダイアログ。"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from shared import github_releases


class ChangelogDialog(tk.Toplevel):
    """選択行のパッケージについて GitHub Releases を非同期取得して表示。"""

    def __init__(
        self,
        master,
        package_name: str,
        current_version: str | None,
        latest_version: str | None,
        repo_url: str | None,
    ):
        super().__init__(master)
        self.title(f'Changelog: {package_name}')
        self.transient(master)
        self.geometry('780x540')

        self.package_name = package_name
        self.current_version = current_version
        self.latest_version = latest_version
        self.repo_url = repo_url

        # ── ヘッダ ─────────────────────────────────────────────────────────
        header = ttk.Frame(self, padding=(8, 8, 8, 4))
        header.pack(fill='x')
        info_text = f'{package_name}: {current_version or "?"}'
        if latest_version and latest_version != current_version:
            info_text += f'  →  {latest_version}'
        ttk.Label(header, text=info_text, font=('TkDefaultFont', 10, 'bold')).pack(side='left')
        ttk.Button(header, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(header, text='Open on GitHub', command=self._open_repo).pack(
            side='right', padx=(0, 4)
        )

        self.status_label = ttk.Label(self, text='Loading releases…', foreground='#666',
                                      padding=(8, 0))
        self.status_label.pack(fill='x')

        # ── 本文 ──────────────────────────────────────────────────────────
        body = ttk.Frame(self, padding=(8, 4, 8, 8))
        body.pack(fill='both', expand=True)
        self.text = tk.Text(body, wrap='word', state='disabled')
        self.text.pack(side='left', fill='both', expand=True)
        vsb = ttk.Scrollbar(body, orient='vertical', command=self.text.yview)
        vsb.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=vsb.set)

        # 整形用タグ
        self.text.tag_configure('tag_h', font=('TkDefaultFont', 11, 'bold'))
        self.text.tag_configure('tag_current', background='#e0f0ff')
        self.text.tag_configure('tag_latest', background='#ffe8c4')
        self.text.tag_configure('tag_in_range', background='#f0fff0')
        self.text.tag_configure('tag_date', foreground='#666')
        self.text.tag_configure('tag_sep', foreground='#aaa')

        self.bind('<Escape>', lambda _e: self.destroy())
        self.after(50, self.grab_set)

        repo = github_releases.parse_repo_url(repo_url)
        if not repo:
            self._set_message(
                'GitHub リポジトリ URL を取得できませんでした。\n\n'
                f'repository: {repo_url or "(none)"}'
            )
            return
        owner, name = repo
        self.status_label.config(text=f'Loading releases from {owner}/{name}…')
        threading.Thread(target=self._load, args=(owner, name), daemon=True).start()

    # ── 取得 / 描画 ──────────────────────────────────────────────────────
    def _load(self, owner: str, repo: str) -> None:
        releases = github_releases.fetch_releases_cached(owner, repo)
        self.after(0, lambda: self._render(releases, owner, repo))

    def _render(self, releases: list[dict], owner: str, repo: str) -> None:
        if not releases:
            self._set_message(
                f'{owner}/{repo} の Release notes を取得できませんでした。\n\n'
                'リポジトリが GitHub に無い、Releases が公開されていない、\n'
                'または API レート制限 (未認証 60 req/h) の可能性があります。\n'
                'Settings の GitHub Token 設定で 5000 req/h に拡張できます。'
            )
            return

        cv = self.current_version
        lv = self.latest_version

        # 範囲内 (cv < v <= lv) を判定するための簡易セット (文字列一致 + 順序判定)
        in_range = self._releases_in_range(releases, cv, lv)

        self.status_label.config(
            text=f'{owner}/{repo} — {len(releases)} releases '
                 f'(highlighted {len(in_range)} between current and latest)'
        )

        self.text.config(state='normal')
        self.text.delete('1.0', 'end')

        for r in releases:
            tag = r.get('tag_name') or '?'
            ver = r.get('version')
            date = (r.get('published_at') or '')[:10]
            body = (r.get('body') or '').strip()
            pre = ' (prerelease)' if r.get('prerelease') else ''

            highlight = None
            if cv and ver == cv:
                highlight = 'tag_current'
                label = '  [current]'
            elif lv and ver == lv:
                highlight = 'tag_latest'
                label = '  [latest]'
            elif ver in in_range:
                highlight = 'tag_in_range'
                label = ''
            else:
                label = ''

            self.text.insert('end', tag, 'tag_h')
            if highlight:
                self.text.insert('end', f'{label}{pre}\n', highlight)
            else:
                self.text.insert('end', f'{pre}\n')
            self.text.insert('end', f'  {date}\n', 'tag_date')
            self.text.insert('end', '\n')
            self.text.insert('end', body or '(no release body)')
            self.text.insert('end', '\n\n')
            self.text.insert('end', '─' * 70 + '\n\n', 'tag_sep')

        self.text.config(state='disabled')
        self.text.see('1.0')

    def _releases_in_range(self, releases, cv, lv) -> set[str]:
        """current < v <= latest を満たす version 集合を粗く抽出 (semver なし簡易比較)。"""
        if not cv or not lv:
            return set()
        from node.core import semver
        cur = semver.parse(cv)
        lat = semver.parse(lv)
        if not cur or not lat:
            return set()
        out: set[str] = set()
        for r in releases:
            v = semver.parse(r.get('version'))
            if not v:
                continue
            if v.gt(cur) and (lat == v or lat.gt(v)):
                out.add(r.get('version'))
        return out

    def _set_message(self, msg: str) -> None:
        self.status_label.config(text='')
        self.text.config(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('end', msg)
        self.text.config(state='disabled')

    def _open_repo(self) -> None:
        url = self.repo_url or ''
        url = url.replace('git+', '').replace('git@github.com:', 'https://github.com/')
        if url.endswith('.git'):
            url = url[:-4]
        if not url:
            url = f'https://www.npmjs.com/package/{self.package_name}'
        webbrowser.open(url)
