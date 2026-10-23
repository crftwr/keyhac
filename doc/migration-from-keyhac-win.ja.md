# Keyhac for Windows (1.x) の config.py を Keyhac 2 に移行する

[English](migration-from-keyhac-win.md) \| 日本語

Keyhac 2 は、1.x の camelCase の API ではなく、（Keyhac for macOS で導入された）刷新された snake_case の API を採用しています。
互換レイヤーはないので、1.x の設定には書き換え作業が必要です。作業は機械的です。概念は同じで、ほとんどの名前は
1 対 1 で対応し、キー表現は変わりません（短縮形のモディファイア `C-`、`A-`、
`S-`、`W-`、`U0-` … も引き続き使えます）。

エントリポイントは同じ (`def configure(keymap):`) ですが、ファイルの場所が変わりました。
Keyhac 2 が読むのは `%APPDATA%\Keyhac\config.py` ではなく `~/.keyhac/config.py` です。書き換えの前に
設定をそこへコピーしてください。初回起動時、Keyhac 2 は 1.x のファイルを見つけると、
そのコピーを代わりに行うかどうかを尋ねます。クリップボード履歴と各種設定も一緒に移ります
（`~/.keyhac/clipboard.json`／`settings.json`）。1.x の ini とその履歴は取り込まれません。

書き換えが終わるまで、コピーした設定は読み込みに失敗します。コンソールウィンドウにエラーが表示され、
すべてのキーはそのまま通されるので、操作できなくなることはありません。いつでもファイルを削除すれば
標準のテンプレートに戻せます。

ポータブルモードも引き継がれています。`Keyhac.exe` の隣にある `config.py` は、1.x とまったく同じく
ユーザーごとのディレクトリより優先されます。
[installation.ja.md](installation.ja.md#ポータブルモード-windows) を参照してください。

## 対応表

| Keyhac 1.x (Windows) | Keyhac 2 |
|---|---|
| `keymap.defineWindowKeymap(exe_name="notepad.exe", class_name="Edit", window_text=…)` | `keymap.define_keytable(app="notepad", class_name="Edit", title=…)` |
| `keymap.defineWindowKeymap()` | `keymap.define_keytable(focus_path_pattern="*")` |
| `keymap.defineMultiStrokeKeymap(help_string)` | `keymap.define_keytable(name=…)`（入力待ちの間、名前がバルーンに表示される） |
| `keymap.replaceKey(src, dst)` | `keymap.replace_key(src, dst)` |
| `keymap.defineModifier(key, mod)` | `keymap.define_modifier(key, mod)` |
| `keymap.InputKeyCommand("C-X")` | 単純な割り当て: `kt["…"] = "Ctrl-X"`（シーケンスにはタプル） |
| `keymap.InputTextCommand(s)` | `InputText(s)` |
| `keymap.ActivateWindowCommand(exe_name=…)` | `ActivateWindow(app=…)` |
| `keymap.MouseMoveCommand(dx, dy)`, `MouseButtonClickCommand(…)`, `MouseWheelCommand(…)` | `MouseMove(dx, dy)`, `MouseButtonClick(…)`, `MouseWheel(…)`, `MouseHorizontalWheel(…)` |
| `keymap.MoveWindowCommand(…)`／モニター端へのコマンド | `MoveWindow(direction=…, distance=…, window_edge=…, screen_edge=…)`, `SnapWindow(position)` |
| `keymap.ShellExecuteCommand(None, "app.exe", …)` | `LaunchApplication("app.exe")`。verb やパラメーターが必要なら関数の中で `subprocess` を使う |
| `keymap.command_ClipboardList` | `ShowClipboardHistory()` |
| `cblister_FixedPhrase(…)` | `ShowClipboardSnippets([...])` |
| リストウィンドウでの `Delete`、`keymap.command_ClipboardRemove` | チューザーのエントリ上で `Shift-Delete`。コードからは `keymap.clipboard_history.remove_item(text)` |
| `keymap.command_RecordStart/Stop/Toggle/Play` | `StartRecordingKeys()` / `StopRecordingKeys()` / `ToggleRecordingKeys()` / `PlaybackRecordedKeys()` |
| `JobQueue` / `JobItem` | `ThreadedAction`（`starting()` / `run()` / `finished()` を持つサブクラスを作る） |
| `keymap.delayedCall(func, msec)`, `CronItem` / `CronTable` | 組み込みの代替はない。`threading` でスケジュールし、`keymap.call_on_main_thread(func)` でコールバックを戻す。[タイマー](#タイマー-delayedcall--crontable)を参照 |
| `keymap.popBalloon(name, text, timeout)` / `closeBalloon(name)` | `keymap.pop_balloon(…)` / `keymap.close_balloon(…)`。従来どおりキャレット位置に表示され、`anchor=` で別の位置を指定できる |
| `keymap.popListWindow(listers)`（ブロッキング） | `ChooserAction` のサブクラスを作る（コールバック方式。ブロッキングするリストウィンドウはない） |
| `keymap.getWindow()`, `pyauto.Window` | `keymap.get_active_window()` / `find_window(…)` / `list_windows()`: ポータブルな `Window` オブジェクト。生の HWND のラッパーは `focus.native` |
| `keymap.editor = "…"` | 変更なし |

## 引き継がれないもの

- **`keymap.delayedCall(func, msec)`** と **`CronItem`/`CronTable`**: 組み込みの代替はありませんが、
  `keymap.call_on_main_thread()` があるので、どちらも `threading` の数行で書けます。
  下の[タイマー](#タイマー-delayedcall--crontable)を参照してください。
- **`keymap.setFont` / `keymap.setTheme`**: UI のテーマやフォントの設定は、まだ変更できません
  （issue トラッカーで管理しています）。
- リストウィンドウでの **Migemo マッチング**: `pymigemo` がインストールされていればチューザーで使えます。
  必須の依存関係ではなく、ない場合の絞り込みは複数語の部分文字列一致だけです。1.x の `*` / `?` のワイルドカードは
  `WildcardMatcher` として使えます。
- **ブロッキングする `popListWindow`**: 意図的に廃止しました。入れ子のメッセージループが、1.x における
  再入バグの最大の原因でした。代わりに `ChooserAction` を使います。
- **`keymap.wnd`**、`send_input_on_tru` の ini による回避策、プロファイルモードのフラグの意味: 廃止しました。

## タイマー (delayedCall / CronTable)

Keyhac 2 には独自のタイマー API がありません。必要ないからです。`threading` でスケジュールし、
`keymap.call_on_main_thread(func)` でコールバックをメインスレッドに戻してください。後者が重要な半分です。
これはスレッドセーフで、1.x の `delayedCall` とまったく同じく、コールバックからウィンドウ、UI、エンジンに
安全に触れられるようにするものです。

```python
import threading
import time

def configure(keymap):

    # delayedCall(func, msec) — one-shot
    def call_later(seconds, func):
        timer = threading.Timer(seconds, lambda: keymap.call_on_main_thread(func))
        timer.daemon = True   # so a pending timer never delays quitting
        timer.start()
        return timer          # .cancel() if you want to call it off

    # CronItem / CronTable — periodic
    def every(seconds, func):
        def tick():
            while True:
                time.sleep(seconds)
                keymap.call_on_main_thread(func)
        threading.Thread(target=tick, daemon=True).start()

    call_later(0.5, lambda: logger.info("half a second later"))
    every(60, lambda: logger.info("once a minute"))
```

知っておくべきことが 2 つあります。

- 遅延させるために `ThreadedAction.run()` の中で sleep **しないでください**。そのプールはすべてのスレッド化された
  アクションが共有する 1 つのワーカーなので、sleep はそのすべてを止めてしまいます。そこで周期的な `while True:` の
  ループを回すと、プロセスが終わるまでワーカーを占有し続けます。
- 開始したタイマーは、プロセスが終了するまで動き続けます。設定を再読み込みしてもタイマーは
  **キャンセルされない** ので、読み込み時にスケジュールしたタイマーは、再読み込みのたびに改めてスケジュールされます。
  モジュールスコープに置くか、問題になる場合は自分でキャンセルしてください。

## 得られるもの

- 同じ設定が macOS でも動きます。OS 固有の部分は `keymap.platform` で分岐します。
- ポータブルな `app=` / `title=` のフォーカス条件と、UI Automation によるフォーカスパスの照合
  （HWND のツリーが不透明な UWP/Electron/Chrome のウィンドウの中でも動きます）。
- `focus.element`: フォーカスのあるコントロールへのスクリプトからのアクセス（Windows では UI Automation）、
  `SnapWindow` によるタイル配置、バルーンで補助されるマルチストロークのテーブル、User2/User3、
  `F21`–`F24`。

完全な API は [configuration.ja.md](configuration.ja.md) にあります。
