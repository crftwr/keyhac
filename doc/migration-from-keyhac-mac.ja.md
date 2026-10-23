# keyhac-mac の config.py を Keyhac 2 に移行する

[English](migration-from-keyhac-mac.md) \| 日本語

## 1.x からの上書きインストール

Keyhac 2 は独自のアプリケーション識別子 (`crftwr.Keyhac2`) で配布されているため、macOS は新しいアプリとして扱います。
初回起動時に System Settings → Privacy & Security → Accessibility で **アクセシビリティ権限を改めて許可** してください。
そこにすでにある *Keyhac* のエントリは 1.x のもので、Keyhac 2 には効きません。1.x を削除したら、このエントリも削除してください。

両方を残さず、1.x はアンインストールしてください。2 つは `~/.keyhac/config.py` と
`~/.keyhac/clipboard.json` を共有し、それぞれが独自のキーボードフックを組み込むので、同時に動かすと
すべてのキー入力に 2 つのフックがかかります。設定ファイルの共有は意図的なものです。既存の
`config.py` は、下に挙げる違いを除いてそのまま読み込まれます。

## 設定の違い

keyhac-mac の設定のほとんどは、わずかな修正で動きます。わかっている違いは次のとおりです。

| keyhac-mac | Keyhac 2 |
|---|---|
| `keymap.focus` -> `UIElement` | `keymap.focus` -> ポータブルな `Focus`。UIElement は `keymap.focus.native` |
| `custom_condition_func(elm)` は `UIElement` を受け取る | `Focus` を受け取る。未知の属性は `.native` に転送されるので、既存の AX ベースの条件はそのまま動く |
| `Hook`、`Console`、`Chooser`、`Clipboard` のコアオブジェクト | 公開していない。アクション／`keymap.pop_balloon`／`getLogger` を使う |
| `MoveWindow(...)` | 完全に移植。window_edge/screen_edge を含めて同じシグネチャ、x/y は非推奨 |
| `keymap.replay_buffer`（非公開） | 同じ名前で、ドキュメント化された |
| `ThreadedAction`、`ShowClipboard*`、`LaunchApplication`、`define_keytable`、`replace_key`、`define_modifier`、`Fn-`/`Cmd-` を含むキー表現 | 変更なし |
| — | 新規: `app=`/`title=` のフォーカス条件、keyhac-win の短縮形（`C-`、`A-`...）、`InputText`、`ActivateWindow`、マウス出力、バルーン、`SnapWindow`、ポータブルな `Window` オブジェクト、User2/User3 |

同じ config.py は Windows でも動きます。`keymap.platform` で分岐してください。macOS の設定のうち次の 3 つは
Windows に持ち越せず、Keyhac 2 は読み込み時にそれぞれを報告します。

- **`Cmd`/`Fn` キー** (`"O-RCmd"`, `keymap.define_modifier("RCmd", ...)`): Windows にはそのキーがありません。
  `Invalid key expression: O-RCmd (… that key exists only on macOS …)`。
- **`Cmd-`/`Fn-` モディファイア**: Windows でも *解析* はされますが（モディファイア名は OS に依存しません）、
  そのビットを立てるキーがないので、その割り当ては何も言わずに決して発火しません。読み込み後、
  Keyhac は一度だけ警告します: `No key produces the Cmd, Fn modifiers on windows …`。
- **フォーカスに対する AX の呼び出し**（`focus.get_attribute_value("AXWindow")`、`get_attribute_names`、
  `perform_action`）: Windows では `Focus.native` は Win32 ウィンドウのラッパーなので、これらは
  `AttributeError` を送出します。`custom_condition_func` の中では、トレースバックが設定の読み込みごとに
  一度だけログに記録され、条件は False と評価されます。

Windows で対応するのは、`Win`/`Apps` キー、`app=`/`title=`/`class_name=` のフォーカス条件、そして
要素の操作には、AX の語彙ではなく UI Automation の語彙
（`ControlType`/`Name`/`Value`/`SelectedText`、`perform_action("Invoke")`）を使う `focus.element` です。
対応表は [configuration.ja.md](configuration.ja.md#focus-オブジェクト) を参照してください。
ウィンドウ操作（`keymap.get_active_window()`、`find_window()`、`MoveWindow`、
`ActivateWindow`）はポータブルなので、分岐は不要です。
