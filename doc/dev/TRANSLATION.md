# Translations of README and the user docs

README.md and the user docs in `doc/*.md` are also published in Japanese
(#283). English is the source of truth; a Japanese page is a translation of
it, never a page of its own with extra or different content. Same scheme as
XeFM's.

These stay English only:

- `doc/dev/` — this directory
- `doc/config-api.md` and `doc/action-api.md` — generated from the docstrings
  by `make api-reference`; a translation would drift on every regeneration
- `doc/config-py-template.md` — the Pages-only page that renders
  `keyhac/_config.py`

`tools/check_translations.py` lists the doc pages it skips in `UNTRANSLATED`.

## Files

Each translation sits next to its source with `.ja` before the extension:

```
README.md                  README.ja.md
doc/installation.md        doc/installation.ja.md
doc/configuration.md       doc/configuration.ja.md
doc/dev/...                (not translated)
```

Images are shared: `images/...` links are copied unchanged. The `.<lang>.md`
naming extends to another language if one is ever added.

### No front matter

GitHub renders YAML front matter as a table at the top of the file, so a
translation carries none. The Pages layout (`_layouts/default.html`) derives
the language from the path instead: a page whose source ends in `.ja.md` is
Japanese.

### The switcher line

GitHub has no language switcher of its own, so each page of a translated pair
carries one on the line after its title:

```markdown
# Installation

English \| [日本語](installation.ja.md)
```

```markdown
# インストール

[English](installation.md) \| 日本語
```

The pipe is escaped because kramdown, which builds the Pages site, reads any
line with a bare `|` as a one-row table; GitHub shows `\|` as a plain `|`.
It goes *after* the `#` title, not above it: GitHub Pages takes a page's
`<title>` from a heading at the very start of the file, and a line above it
would leave the page untitled. In README.md, wrap it in
`<!-- pypi-exclude-start -->` / `<!-- pypi-exclude-end -->` so it stays off
PyPI. The Pages layout removes this line and shows its own switcher in the
header instead.

## On the Pages site

The layout, for a page that has a counterpart:

- emits `<html lang="ja">` or `lang="en"`, and `<link rel="alternate"
  hreflang>` for both pages plus `x-default` for the English one
- shows "English | 日本語" in the header, linking to the counterpart
- on the English top page only, redirects to `README.ja.html` when the
  browser's first `en`/`ja` preference in `navigator.languages` is `ja`. A
  language picked in the header switcher is saved in `localStorage`
  (`keyhac-lang`) and wins over the browser's preference from then on. No
  other page redirects, so a shared link keeps its language.

A page with no translation gets none of this and stays English.

## Keeping translations current

- **Change both in one commit.** When README.md or a `doc/*.md` with a
  translation changes, update its `.ja.md` in the same commit (CLAUDE.md has
  the rule).
- **Links go to translations.** A link in a `.ja.md` points to the other page's
  `.ja.md` when that exists, and to the English page otherwise (the API
  references, for one). Anchors change with the headings: `#portable-mode-windows`
  in English is `#ポータブルモードwindows` in Japanese.
- **`make fix-docs`** does that for you. Translate with every link copied
  from the English page as it is; `tools/check_translations.py --fix` turns
  `X.md` into `X.ja.md` wherever the translation exists, maps each `#anchor`
  to the Japanese heading at the same position, and adds a missing switcher
  line to either page. That mapping is why a translation keeps its source's
  headings one for one — same count, same order, same levels. Run it again
  whenever a page gains its translation, so the pages already translated link
  to it.
- **`make check-docs`** (`tools/check_translations.py`) reports pages with no
  translation (a warning; `--strict` makes it an error), a pair missing its
  switcher line, links to an English page that has a translation, anchors
  that match no heading in their target, emphasis that will not close before
  Japanese text, and translations whose source changed after them (by last
  commit; uncommitted changes count as newer than any commit, so a pair edited
  together passes).

## Style

- です・ます調. Plain, direct sentences; don't add politeness the English
  doesn't have.
- A half-width space between Japanese text and Latin letters or digits
  (`Keyhac の設定`, `3 つ`) except next to full-width punctuation.
- Full-width `、。（）「」`; half-width `:` and `()` inside code or Latin-only text.
- Long-vowel marks as in Microsoft's Japanese style guide: ユーザー,
  フォルダー, サーバー, モニター, セレクター, エディター; but ディレクトリ,
  メモリ, エントリ.

### Not translated

Keyhac's interface is English only, so anything the reader has to find on
screen or type stays exactly as in the English page:

- key names and key expressions — `Enter`, `Fn-J`, `Cmd-Shift-V`, `User0`
- menu, dialog, button and checkbox labels — **Edit Config**,
  **AI Integration > MCP Server**, **Advanced > Sync Python Packages**; and
  OS settings labels — **Privacy & Security > Accessibility**
- class, function, argument and setting names — `define_keytable`,
  `focus_path_pattern=`, `ThreadedAction`, `ShowClipboardHistory`
- code, commands, file names, paths, URLs, environment variables
- product names — Keyhac, PuiKit, Claude Desktop, VS Code, Homebrew
- code blocks, comments in them included — a reader copies them into
  `config.py` as they are

Where a label needs explaining, add the Japanese after it once:
**Reload Config**（設定の再読み込み）.

## Glossary

Use these renderings everywhere, so one English term reads as one Japanese
term across the docs. Add a row when a new recurring term comes up.

| English | 日本語 | Notes |
|---|---|---|
| Accessibility permission | アクセシビリティ権限 | the OS label stays **Accessibility** |
| accessibility tree | アクセシビリティツリー | |
| action | アクション | an `Action` / `ThreadedAction` |
| AI assistant / agent | AI アシスタント / エージェント | |
| AI integration | AI 連携 | |
| application, app | アプリケーション、アプリ | |
| armed (prefix, multi-stroke table) | 入力待ち | |
| assignment | 割り当て | `kt["Fn-J"] = …` |
| authoring | オーサリング | |
| background work | バックグラウンド処理 | |
| balloon | バルーン | |
| bind / key binding | 割り当てる / キー割り当て | |
| bridge | ブリッジ | the stdio bridge |
| bundle / app bundle | バンドル / アプリバンドル | |
| candidate / candidate window (IME) | 候補 / 候補ウィンドウ | |
| caret | キャレット | |
| chooser | チューザー | the popup list; first use: チューザー（選択ウィンドウ） |
| client (MCP) | クライアント | |
| clipboard / clipboard history | クリップボード / クリップボード履歴 | |
| compose (IME) | 変換する | |
| configuration, config | 設定 | `config.py` stays as is |
| consent | 同意 | |
| console window | コンソールウィンドウ | |
| consume (a key) | 消費する | |
| Contact & Support | 連絡先とサポート | as a heading |
| default | 既定 / 既定値 | never デフォルト |
| deprecated | 非推奨 | |
| Documentation | ドキュメント | as a heading |
| element (UI) | 要素 | |
| elevated (administrator) | 管理者権限で動く | |
| endpoint | エンドポイント | |
| extension (`~/.keyhac/extensions/`) | 拡張 | |
| filter (verb) / filter field | 絞り込む / 絞り込み欄 | |
| fire (a binding) | 発火する | |
| focus / focus path | フォーカス / フォーカスパス | |
| focus condition | フォーカス条件 | `app=`, `title=`, … |
| hook / keyboard hook | フック / キーボードフック | |
| IME | IME | |
| inject | 注入する | |
| input source (macOS) | 入力ソース | |
| inspector | インスペクター | |
| key expression | キー表現 | `"Ctrl-Shift-A"` |
| key table | キーテーブル | |
| keyboard macro | キーボードマクロ | |
| keymap | キーマップ | the `keymap` object stays in code |
| launch | 起動 | |
| log / log level | ログ / ログレベル | |
| loopback | ループバック | |
| main thread / worker thread | メインスレッド / ワーカースレッド | |
| menu-bar extra (macOS) | メニューバーアイコン | |
| migration | 移行 | |
| modifier | モディファイア | "modifier key" → モディファイアキー |
| monitor / screen | モニター / 画面 | |
| multi-stroke | マルチストローク | |
| one-shot modifier | ワンショットモディファイア | |
| pass through (a key) | そのまま通す | noun: パススルー |
| permission | 権限 | |
| persist | 永続化 | |
| portable mode | ポータブルモード | |
| precondition | 事前条件 | per-step checks in an action |
| prefix key | プレフィックスキー | |
| preview | プレビュー | |
| privacy | プライバシー | |
| query | クエリ | |
| remap | リマップ | |
| Requirements | 動作環境 | as a heading |
| second stroke | 2 打目 | |
| See also / Related | 関連項目 | as a heading |
| selector | セレクター | |
| settings (`settings.json`) | 各種設定 | keeps it apart from 設定 = config |
| skill (AI agent) | スキル | |
| snapshot | スナップショット | |
| snippet | 定型文 | |
| source (chooser) | ソース | |
| step (action) | ステップ | |
| sync (Python packages) | 同期 | |
| task tray (Windows) | タスクトレイ | "tray menu" → トレイメニュー |
| template | テンプレート | |
| token | トークン | |
| traceback | トレースバック | |
| Translation table | 対応表 | migration guide |
| transport (MCP) | トランスポート | |
| troubleshooting | トラブルシューティング | |
| uninstall | アンインストール | |
| user modifier | ユーザーモディファイア | |
| verified / untried (clients) | 検証済み / 未検証 | |
| virtualenv | 仮想環境 | |
| window | ウィンドウ | |
| work area | 作業領域 | |
