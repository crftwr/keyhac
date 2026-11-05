# 設定

[English](configuration.md) \| 日本語

Keyhac は 1 つの Python ファイル `~/.keyhac/config.py`（Windows の
[ポータブルモード](installation.ja.md#ポータブルモード-windows)では、`Keyhac.exe` の隣にある
`config.py`）で設定します。初回起動時に、全体にコメントを付けたテンプレートからこの
ファイルが作られます。テンプレート（ソースツリーの
[keyhac/_config.py](../keyhac/_config.py)）は、このページで説明するすべての機能の動く例に
なっているので、読みながら開いておくとよいでしょう。

このページでは、API を出会う順に紹介します。個々の API の正確な引数については
[API リファレンス](config-api.md)を参照してください。

## 設定ファイル

```python
from keyhac import *

def configure(keymap):
    ...
```

Keyhac は起動時と、再読み込み（トレイ / メニューバーのメニューの「Reload Config」）のたびに
`configure(keymap)` を呼び出します。エラーの影響は限定されます。

- 読み込みに失敗した設定では、**以前の** キーマップが有効なまま残ります。エラーは
  コンソールウィンドウに表示されます。
- 割り当てた関数の中で例外が起きると、トレースバック付きでログに記録され、キーは
  変更されずにそのまま通されます。設定が壊れていても、入力は動き続けます。

`~/.keyhac/extensions/` は `sys.path` に含まれているので、大きな設定をモジュールに分割して
`import` できます。再読み込みではそれらも再インポートされるので、拡張の編集は Keyhac を
再起動しなくても反映されます。

このディレクトリは `sys.path` の先頭ではなく **末尾に追加** されます。拡張はその機能に
ちなんだ名前を付けるもので、そうしないと、キューを扱うアクションの隣に置いた `queue.py` が
プロセス全体で標準ライブラリを隠してしまうからです。Python がすでに提供しているモジュールと
同じ名前を付けると、Python のものが優先され、自分のモジュールには到達できません。名前を
変えてください。

### 追加の Python ライブラリ

設定と拡張からは、標準ライブラリと Keyhac 自体が同梱しているものをインポートできます。
それ以外のものは、`config.py` の隣の `~/.keyhac/requirements.txt` に列挙し、トレイ /
メニューバーのメニューで **Advanced > Sync Python Packages** を選びます。

```
# ~/.keyhac/requirements.txt
requests
openpyxl
```

同期はバックグラウンドで pip を実行し、Keyhac 自体が動いている Python を使うので、
コンパイル済みのパッケージもそれに合ったものになります。pip の出力はコンソールに表示され、
完了すると設定が再読み込みされます。Keyhac を pip でインストールした場合は、シェルから
`keyhac deps sync` で同じことができ、`keyhac deps status` は同期が必要かどうかを
（オフラインで）知らせます。

macOS アプリや Windows アプリを含め、どのインストール形態でも同じように動作します。
ライブラリは Keyhac の中ではなく `~/.keyhac/site-packages/` に入るので、Keyhac を
アップグレードしても残ります。

- **何がインストールされるかはファイルが決めます。** 行を削除して同期すると、その
  ライブラリは削除されます。空のファイル（またはファイルがない状態）では、すべて削除されます。
- **Keyhac 自身のライブラリが優先されます。** 列挙したライブラリが、Keyhac がすでに
  使っているライブラリの別のバージョンを必要とする場合、同期は失敗し、コンソールに双方の
  名前が表示されます。同期が成功するまで何も変わりません。
- **Keyhac がオンラインになるのは同期のときだけで、それもユーザーが開始したときだけです。**
  起動時と再読み込みのたびに、Keyhac は前回の同期以降にファイルが変更されたか、Keyhac が
  アップグレードされたかをオフラインで確認します。そうであれば、同期が必要だとコンソールに
  表示します。Keyhac が自分から同期することはありません。
- **読み込み済みのライブラリは再起動で更新されます。** `extensions/` と違い、これらは
  再読み込みで再インポートされません。ライブラリとしてはこれが正しい動作です。すでに
  インポートされたライブラリは、Keyhac を再起動するまで古いバージョンのままです。
  Windows では、使用中のライブラリを置き換えられない場合、新しいセットは次回の起動時に
  有効になります。
- **新しい Python バージョンでは同期が 1 回必要です。** ライブラリは Python のバージョン
  ごとに保持されます（`site-packages/3.14/`）。新しい Keyhac が新しい Python で動くときは
  コンソールにそう表示され、1 回同期すれば同じリストが再びインストールされます。

`requirements.txt` は通常の pip の requirements ファイルです。バージョン指定、
`--index-url`、ファイルからの相対パスはすべて使えます。`-r` で取り込んだファイルは起動時の
確認の対象にならないので、それを編集したあとは手動で同期してください。`site-packages/` を
自分で編集しないでください。同期のたびに置き換えられます。`~/.keyhac` を dotfiles
リポジトリで管理している場合は、`requirements.txt` をコミットし、`site-packages/` は
含めないでください。各マシンがそれぞれ同期します。

トレイメニューの「Edit Config」は、ファイルをエディターで開きます。使うエディターは
次のように指定します。

```python
keymap.editor = "CotEditor"          # app name or path, or a callable(path)
```

指定しない場合、macOS では VS Code、Xcode、TextEdit の順に試します。Windows では
メモ帳を使います。

## 1 つの設定で両方の OS に対応する

同じファイルが Windows と macOS の両方で動きます。OS が本当に異なる部分では、
`keymap.platform`（`"windows"` または `"mac"`）で分岐します。ほとんどは 2 つの定数で
吸収できます。これはテンプレート全体で使われているパターンです。

```python
mac = keymap.platform == "mac"
MOD1 = "Fn" if mac else "User0"     # the modifier your bindings hang off
MOD2 = "Cmd" if mac else "Ctrl"     # the OS's primary shortcut modifier

kt[f"{MOD1}-C"] = f"{MOD2}-C"
```

ここでの答えは組み込みの名前ではなく、普通の Python の定数です。キー表現の言語に `Mod-`
モディファイアはありません。モディファイア名はどちらの OS でも同じ意味を持ちます。OS ごとに
こっそり意味が変わるモディファイアがあると、往復で整合しなくなります。コンソールは実際に
発火したもの、つまり `Cmd-C` か `Ctrl-C` を報告するからです。定数なら、設定の中で OS ごとに
変えたいほかのものにもそのまま広げられます。上の `MOD1` が同じやり方で機能しているのは
そのためです。この名前はテンプレートのものです。定数に別の名前を付けたり、
`keymap.platform` の確認をその場に書いたりしている設定も、同じことをしています。

設定の診断はクロスプラットフォームを考慮しています。もう一方の OS にしかないキー名を使うと
エラーでそのことが示されます。また、読み込み後、この OS のどのキーでも作り出せない
モディファイアを使った割り当て（たとえば Windows で動いている `Cmd-` の割り当て。構文としては
正しいものの、黙って一度も発火しません）について、Keyhac は一度だけ警告します。

## キーテーブル

キーテーブルは、キー割り当てと、それが適用される条件を保持します。

```python
# global (matches everything)
kt = keymap.define_keytable(focus_path_pattern="*")

# portable: by application, by window title (fnmatch wildcards, "|" alternation,
# case-insensitive; ".exe" optional on Windows)
kt_term = keymap.define_keytable(app="WindowsTerminal|Terminal|iTerm2")
kt_edit = keymap.define_keytable(app="Code", title="*myproject*")

# platform extras
kt_np    = keymap.define_keytable(app="notepad", class_name="Edit")        # Windows
kt_area  = keymap.define_keytable(focus_path_pattern="*/AXTextArea(*)")    # macOS

# arbitrary logic
kt_x     = keymap.define_keytable(custom_condition_func=lambda focus: ...)

# named, detached table = a multi-stroke second stroke (see below)
kt_ctrlx = keymap.define_keytable(name="Ctrl-X")
```

照合の対象:

- `app` — Windows ではプロセス / exe のベース名、macOS ではローカライズされた
  アプリケーション名。
- `title` — ウィンドウタイトル。
- `class_name` — Win32 のウィンドウクラス（Windows のみ）。
- `focus_path_pattern` — フォーカスされた要素に至るまでのコントロールの階層。macOS では
  AX ツリー、Windows では UI Automation ツリーです。いろいろなものにフォーカスを移しながら
  コンソールの「Focus path」欄を見ると、現在の値がわかります。

  ```
  macOS:    /AXApplication(Xcode)/AXWindow(...)/.../AXTextArea()
  Windows:  /Application(Code)/Window(...)/.../Edit(Message input)
  ```

  階層を飛ばすには `*` を使います: `focus_path_pattern="*/Edit(*)"`。各要素は
  `Role(Name)` の形で、多くのコントロールには名前があるので、`*/Edit()` は名前のない
  ものにしか一致しません。通常は `*/Edit(*)` を使います。

  フォーカスされた要素はキーイベントのたびに読み取られるので、1 つのウィンドウの *中で*
  フォーカスが動いたとき（リストの項目、タブ、Chromium / Electron / WPF / WinUI のページ上の
  入力欄など）も、どちらのプラットフォームでも条件が評価し直されます。前面のアプリケーションが
  応答しなくなると、応答が戻るまで、パスはウィンドウのレベル（`/Application(…)/…(title)`）に
  フォールバックします。
- `custom_condition_func(focus)` — 独自の判定。[Focus
  オブジェクト](#the-focus-object)を受け取ります。フォーカスなしで呼ばれることはありません。
  フォーカスを報告するものがない間、テーブルは単に無効になります。

**アプリケーションの呼び名。** `app=` は `Focus.app_name` に対して照合され、この値は
どこで目にしても同じです。`custom_condition_func` が `focus.app_name` として受け取る値、
`list_windows` のアプリケーション列、`describe_keymap`（MCP ツール）の `focus now:` 行、
そしてコンソールのフォーカスパスです。

| | `Focus.app_name` の値 | 例 |
|---|---|---|
| Windows | 実行ファイルの名前（".exe" **なし**） | `Code`, `WindowsTerminal`, `notepad`, `msedge`, `cmd` |
| macOS | アプリケーションのローカライズされた表示名 | `Code`, `Terminal`（日本語のシステムでは `ターミナル`）, `Safari`, `Google Chrome` |

`app=` は寛容です。大文字と小文字を区別せず、ワイルドカードと `|` が使え、Windows では
パターンの ".exe" は省略できます。自分で書く関数はただの Python なので、文字列を厳密に
比較します: `focus.app_name == "WindowsTerminal"` であり、`"WindowsTerminal.exe"` では
ありません。迷ったら、推測せずに、そのアプリケーションにフォーカスを移して上に挙げた
いずれかの場所で名前を読み取ってください。

**一致するテーブルはすべて同時に有効になり**、定義順にマージされます。キーごとに、あとの
テーブルが優先されます。グローバルなテーブルを最初に定義し、アプリ固有のテーブルをその
あとに定義すれば、固有のテーブルは自分が割り当てたキーだけをちょうど上書きします。

### 1 つのアプリケーションを除くすべての場所

否定の `app=` はありません。「ここ以外のすべて」は `custom_condition_func` で表し、
例外には意味がそのまま読める名前を付けます。

```python
def is_terminal(focus):
    return focus.app_name in ("Terminal", "ターミナル", "iTerm2", "WindowsTerminal",
                              "cmd", "powershell", "pwsh")

kt_emacs = keymap.define_keytable(
    custom_condition_func=lambda focus: not is_terminal(focus))
kt_emacs["Ctrl-F"] = "Right"
kt_emacs["Ctrl-B"] = "Left"
```

設定にすでにそのような関数がある場合（テンプレートの `is_terminal` がその一例です）は、
それを再利用して、「ターミナル」がファイル全体で 1 つの意味になるようにしてください。
関数ではなくターミナル用のテーブル（`define_keytable(app="Terminal|iTerm2|…")`）がある場合は、
そのパターンにある名前が関数に書くべきリストです。パターンそのものを否定する方法は
ありません。ローカライズされた名前は別の名前なので、使っているマシンが報告する綴りを
すべて列挙してください（`Terminal` と `ターミナル`）。

もう 1 つの方法は、すべての場所に適用されるテーブルでキーを割り当て、例外用のあとの
テーブルで `None` を使ってキーを返すことです。`None` はキーに手を加えずにそのまま通します
（[割り当て](#割り当て)を参照）。

```python
kt["Ctrl-F"] = "Right"                  # everywhere ...
kt_terminal["Ctrl-F"] = None            # ... except here, where Ctrl-F is Ctrl-F
```

どちらも機能します。例外がキーのセット全体に及ぶときは、最初の方法を選んでください。
1 か所にまとまり、あとでセットに追加したキーも 2 行目を書かずに除外されます。2 番目の方法は、
ほかの場所ではすべて正しいテーブルについて、1 つのアプリケーションで 1 つか 2 つのキーを
返す必要があるときに向いています。キーをそれ自身に割り当てて（`kt_terminal["Ctrl-F"] = "Ctrl-F"`）
返そうとは **しないでください**。それはパススルーではなく、以下で説明するように、Keyhac が
消費して送り直すキーになります。

## キー表現

`{O-|D-|U-}{Modifier-}...{Key}` — 大文字と小文字を区別しません。

- **モディファイア**: `Alt`、`Ctrl`、`Shift`、`User0`–`User3`、それに加えて macOS では
  `Cmd` と `Fn`、Windows では `Win`。それぞれに `L`/`R` の変種があります（`LCtrl-`、`RAlt-`）。
- **短縮形** も別名として受け付けます: `A-`、`C-`、`S-`、`W-`、`U0-`–`U3-`、
  およびその `L`/`R` 形（`LC-`、`RA-`、…）。文書で使う綴りは完全な名前です。
- **左右の規則**: 特に指定しない限り、条件は左右を区別しません。`Ctrl-A` は左右どちらの
  Ctrl にも一致し、`LCtrl-A` は左のみに一致します。出力は左側のモディファイアキーで送られます。
- **プレフィックス**: `O-` はワンショット（後述）、`D-` はキーダウンのみ、`U-` はキーアップのみ。
- **キー名**: 英字、数字、記号（`Semicolon`、`Slash`、`OpenBracket`、…、および `Yen` や
  `Atmark` のような JIS の名前）、`F1`–`F20`（Windows では `F24` まで）、`Return`
  （`Enter` も使えます）、`Escape`、`Tab`、`Space`、`Back`（Backspace）、`Delete`、
  ナビゲーションキー群（`Left`、`Home`、`PageUp`、…）、テンキー（`Num0`、`NumAdd`、…）、
  `Kana`/`Eisu`/`Menu`/`Help`（macOS。`Menu` は PC キーボードが送るアプリケーションキー
  です）、`Apps`/`PrintScreen`/`ScrollLock`/`Pause`（Windows）、そしてモディファイアキー
  そのものを主キーとして（`LWin`、`RCmd`、…）。対応付けのないキーは、生のコードで
  表せます: `"(124)"`。
- **macOS の Fn-矢印キーの落とし穴**: Apple のキーボードは `Fn-Left/Right/Up/Down` を
  ハードウェアで `Home`/`End`/`PageUp`/`PageDown` に変換します（`Fn` モディファイア自体は
  届きます）。そのため `Fn-…-Left` の割り当ては決して発火しません。代わりに `Fn-…-Home` を
  割り当ててください。テンプレートの MoveWindow のサンプルに、OS ごとの書き方があります。
- **macOS のファンクションキー列**: 最上段がメディア機能になっている場合（macOS の既定。
  「Use F1, F2, etc. keys as standard function keys」がオフ）、キーが F1 として
  アプリケーションに届くのは Fn を押しているときだけで、Keyhac に見えるのは `F1` ではなく
  `Fn-F1` です。コンソールの最後のキーの欄には `LFn-F1` と表示されます。このとき、
  「アプリケーションが F1 として受け取るキー」の割り当ては `Fn-F1` です。どちらの設定にも
  対応するには両方を割り当てます。

  ```python
  kt_vscode["F1"] = lambda: None
  if keymap.platform == "mac":
      kt_vscode["Fn-F1"] = lambda: None   # top row on media keys: Fn+F1 is F1
  ```

## 割り当て

```python
kt["Fn-J"] = "Left"                          # key -> key
kt["Fn-N"] = "Cmd-1", "Cmd-2"                # key -> sequence
kt["Fn-A"] = some_callable                   # key -> function / action object
kt["Ctrl-X"] = kt_ctrlx                      # key -> multi-stroke table
kt["O-RCtrl"] = "Space"                      # one-shot
```

呼び出し可能なものなら何でも割り当てられます。普通の関数、ラムダ、あるいは以下の
アクションオブジェクト（`MoveWindow(...)`、`ShowClipboardHistory()`、… — どれも呼び出し
可能です）のいずれかです。

**キーを何もしないようにする。** 何もしない関数を割り当てます。

```python
kt_vscode = keymap.define_keytable(app="Code")
kt_vscode["F1"] = lambda: None          # F1 never reaches VS Code
```

キーは消費され、何も送られません。上のようにテーブルの条件で範囲を絞れば、ほかの
アプリケーションにはこれまでどおりキーが届きます。Mac のファンクションキーでは、
`Fn-F1` も割り当ててください（[ファンクションキー列](#キー表現)を参照）。
「何もしない」を意味しそうに見える値は、別の意味を持ちます。

| 割り当てる値 | キーの動作 |
|---|---|
| `lambda: None` | **何もしない** — 消費され、何も送られません。使うべき書き方です。 |
| `()` | これも何もしませんが、Windows ではそのあとで押していた Alt や Win を離すと、メニューバーやスタートメニューが開くことがあります。関数の形ならそれを防げます |
| `None` | **手を加えずにそのまま通します**。どのテーブルも割り当てていないのと同じで、あとのテーブルでキーを返すための方法です |
| `""` | エラー。押すたびにコンソールにログが出て、キーはそのまま通されます |

**割り当てが送るキーは、アプリケーションに直接届きます。** テーブルと再び照合されることは
ないので、テーブルが自分の出力でループすることはありません。`kt_cx["Ctrl-X"] = "Ctrl-X"` は
Ctrl-X（切り取り）を送り、プレフィックスを再び有効にはしません。キーをそれ自身に割り当てた
場合（`kt["Ctrl-F"] = "Ctrl-F"`）も同様です。キーはそのまま通されるのではなく、消費されて
送り直されます。アプリケーションからはたいてい区別がつきませんが、オートリピートは新しい
押下の連続として届き、合成入力に気づくもの（ゲーム、リモートデスクトップクライアント、
一部のパスワード欄）には、あなたのキーではなく Keyhac のキーとして見えます。キーに手を
付けないままにするには、`None` を割り当てるか、割り当てないでください。（キーボードマクロは
例外です。再生はテーブルを再び通るので、記録したキーは押したときと同じ動作をします。）

**マルチストロークのテーブル**: 名前付きのテーブルを割り当てると、それがプレフィックスとして
入力待ちになります。`Ctrl-X` を押してから、`kt_ctrlx` で割り当てたキーを押します。入力待ちの間は
バルーンにテーブルの名前が表示されます。入力待ちのテーブルで割り当てられていないキーを押すと
入力待ちは解除されます（そのキーは捨てられるので、打ち間違いが余計なキー入力としてアプリに
漏れることはありません）。

2 打目は、ほかのキーとまったく同じく、モディファイアを含めて照合されます。Emacs 流に入力する
ユーザーは両方の打鍵の間 Ctrl を押し続けるので、2 打目のキーは `S` ではなく `Ctrl-S` です。
どちらでも動くようにするには両方を割り当てます。

```python
kt_cx = keymap.define_keytable(name="Ctrl-X")
kt["Ctrl-X"] = kt_cx
kt_cx["Ctrl-S"] = f"{MOD2}-S"      # Ctrl held through: Ctrl-X Ctrl-S
kt_cx["S"] = f"{MOD2}-S"           # Ctrl let go: Ctrl-X S
```

プレフィックスキー自体は、テーブルが適用されるすべてのアプリケーションで、もとの動作を
しなくなります。この例では、Windows では Ctrl-X が切り取りではなくなり、macOS では
アプリケーションが Ctrl-X に割り当てていた動作をしなくなります(macOS の切り取りは
Cmd-X です。テキスト欄は Ctrl-X を Emacs 流のキーとして扱うか、無視します)。
もとの動作を使えるようにしておくには、それ専用の 2 打目を割り当てます。プレフィックスを
2 回押すのが一般的です。プレフィックスそのものを送ると、各システムでのもとのキーの動作に
戻りますが、それは両方で同じではありません。コメントには「切り取り」と書かず、そのことを
書きます。

```python
kt_cx["Ctrl-X"] = "Ctrl-X"         # Ctrl-X Ctrl-X: Ctrl-X as before (Cut on Windows)
```

## リマップとユーザーモディファイア

```python
keymap.replace_key("Insert", "LCtrl")        # swap a key outright
keymap.define_modifier("Apps", "User0")      # turn a key into your own modifier
```

- `replace_key` はほかのすべてより先に実行されます。設定のほかの部分には、置き換え後の
  キーしか見えません。
- `define_modifier` は、キーを `User0`–`User3` のいずれか（どのアプリケーションにも見えない
  モディファイア）として動作させます。定義されている間、そのキーはもとの意味を完全に失う
  （決して送出されない）ので、`User0-J` の割り当てがアプリの理解する何かと衝突することは
  ありません。
- すでにモディファイアであるキーは **選ばないでください**。指定すること自体はできますが、
  そのキーはあらゆる場所で Alt（あるいは Ctrl や Shift）として機能しなくなります。そうなったときは
  ログにそう記録されます。CapsLock もいけません。CapsLock は離したことをすぐに報告するので、
  モディファイアの拠り所となる押下中の状態がありません。
- `define_modifier("LWin", …)` は拒否されます。ユーザーモディファイアはすべてのものから
  見えないことを約束するものですが、Windows キーはそうはなれないからです。Windows キーを
  引退させても、すべての場所で引退するわけではありません。
  - **Win+L は引き続き画面をロックします。** Windows はこの組み合わせを Ctrl+Alt+Del と
    同様に予約していて、どのキーボードフックよりも先に処理します。どのプログラムもこれを
    止められません。
  - **Win+G は引き続き Game Bar を開き**、それを開いたキー入力を飲み込みます。Keyhac が
    注入したものも含みます。

  それ以外はすべて止まります。Win+I や Win+T などは発火せず、どのアプリケーションもその
  キーを受け取りません。サンプルの設定は、Keyhac for Windows が昔からそうしてきたように、
  まず両方の Windows キーを引退させてから User0 に到達します。

  ```python
  keymap.replace_key("LWin", 235)     # 235, 255: unassigned key codes
  keymap.replace_key("RWin", 255)
  keymap.define_modifier(235, "User0")
  ```

  これは意図的なトレードオフです（Windows キーは、どのキーボードでも手放せる唯一のキー
  です）。そして、上の 2 つの例外は変わりません。そのモディファイアの下に割り当てたアクションを、
  `g` の入力から始めないでください。

**ワンショットモディファイア**（`O-` プレフィックス）は、モディファイアキーに 2 つ目の役割を
与えます。ほかのキーと一緒に押したときは通常どおり修飾し、*単独で* タップしたときは
ワンショットの割り当てを発火します。

```python
kt["O-LCmd"] = "Eisu"        # tap left Cmd alone -> IME off; held -> still Cmd
```

ワンショットは自分自身のモディファイアを持つこともできます（文法は
`O-{Modifier-}...{Key}`）。それらはほかの割り当てと同じく厳密に照合されます。そのため、
1 つのキーで 2 方向を操作できます。

```python
kt["O-RCmd"] = next_terminal_window        # tap right Cmd
kt["O-Shift-RCmd"] = prev_terminal_window  # tap it with Shift held: other way
```

それらのモディファイアは、ワンショットのキーが押される *前* に押しておき、キーが離される
まで押し続ける必要があります。「単独でタップ」とは厳密には、キー自身の押下が、それを離す
前の最後の入力イベントだったという意味です。その間に何かがあるとタップは取り消されます。
ほかのキーを押すこと *または離すこと*（モディファイアを含む）、そしてマウスのクリックや
ホイールの回転です。そのため、途中までのショートカットでは発火しません。

モディファイアは条件の一部なので、Shift を押している間は、素の `O-RCmd` は発火しません。
そのときに実行されるのは `O-Shift-RCmd` で、それが割り当てられていなければ何も実行されません。

## 関数から入力を送る

```python
def wrap():
    with keymap.get_input_context() as ctx:
        ctx.send_key("Ctrl-C")               # key expression, incl. modifiers
        ctx.send_text("hello")               # literal text, any characters
```

`get_input_context()` は仮想入力をまとめ、モディファイアを調整してくれます。物理的に押されて
いるモディファイアはまとめた入力の前後で離され、そのあとで元に戻されるので、割り当て自身の
モディファイアがまだ物理的に押されている間でも `ctx.send_key("Ctrl-C")` は動作します。
`ThreadedAction` のワーカースレッドからも安全に使えます。

単純な場合には、既製のアクションがあります。

```python
kt["Fn-Semicolon"] = InputText("me@example.com")   # type a literal string
```

**マウス出力**: `MouseMove(dx, dy)`、`MouseButtonDown/Up/Click(button)`（既定は `"left"`、
または `"right"` / `"middle"`）、`MouseWheel(notches)`、`MouseHorizontalWheel(notches)`
（正の値は奥向き / 右向き、1.0 = 1 ノッチ）。それに対応する `ctx.send_mouse_*` メソッドも
あります。ボタンとホイールは、押されているモディファイアを先に離します（そのため
`User0-…` の割り当てが、ありもしないモディファイア付きでクリックすることはありません）。
移動ではモディファイアはそのままです。相対移動はどちらの OS でもマウスの加速の影響を
受けないように注入され、合成された素早いクリックはダブルクリックとして認識されます。

## IME

```python
if keymap.get_ime_status():          # True on, False off, None can't tell
    keymap.set_ime_status(False)     # returns whether the state was reached
```

どちらも **入力フォーカスを持っているもの** に作用し、どちらもウィンドウを引数に取りません。
それが 2 つの OS の両方が守れる唯一の約束です。Windows はウィンドウハンドルを通じて状態に
アクセスするので背後のウィンドウも扱えますが、macOS が公開するのは「現在の入力ソース」だけ
です。ウィンドウ引数を設ければ、1 つの名前をかぶった 2 つの異なる API になってしまいます。

`get_ime_status()` は、状態を判定できないときに `False` ではなく `None` を返します。IME が
インストールされていない場合や、Windows で IMM32 の問い合わせに応答しない TSF 専用の IME の
場合です。`set_ime_status()` は呼び出しが成功したと仮定せずに状態を読み戻すので、`False` は
IME が拒否したか、問い合わせる IME がなかったことを意味します。

知っておくべき注意点:

- 「オフ」は、どちらの OS でも 2 つの異なる状況を含みます。インストールされていて閉じている
  IME と、IME がまったく関わっていない状態（Windows では素のキーボードレイアウト、macOS では
  素のレイアウトまたは入力メソッドのローマ字モード）です。
- **「オン」の届く範囲は、Windows では macOS ほど広くありません。** macOS は US レイアウトから
  でも日本語の入力ソースを選択します。Windows は、フォーカスのあるウィンドウが *すでに* その
  言語で入力している IME を開くだけなので、en-US で `set_ime_status(True)` を呼ぶと、入力言語を
  切り替えずに `False` を返します。言語の切り替えは、ユーザー自身の Win+Space のままです。
  そのため、移植可能な設定では、`False` を再試行すべき失敗ではなく「ユーザーはいま IME の
  言語にいない」と扱うべきです。
- すでにオンのときに IME を *オン* にしても現在のモードはそのままなので、「オン」を指定する
  割り当てが macOS のユーザーをカタカナからひらがなに引き戻すことはありません。複数の IME が
  インストールされていて IME がオフのとき、「オン」で選ばれるのは有効なもののうち最初の
  IME です。macOS は最近使った順序を公開していません。
- 変更が *ほかの* アプリケーションにも影響するかどうかは、ユーザーの OS の設定（Windows の
  「Let me use a different input method for each app window」、macOS の「Automatically switch
  to a document's input source」）で決まり、Keyhac が決めることではありません。
- **キー出力を「オフ … オンに戻す」で囲まないでください。** 状態の変更はすぐに有効になり
  ますが、`send_key()` はイベントをアプリケーションがあとで受け取るように *キューに入れる*
  だけです。そのため、元に戻す処理のほうが先に効き、キーは結局変換されてしまいます
  （`"git status"` が `"gいt"` として届きます）。待って解決しようとしても解決になりません。
  キーから起動されたアクションはキーボードフックの中でメインスレッド上で実行され、フックの
  タイムアウトを超えてスリープするとフックが外されてしまいます。文字列そのものを入力するには
  `ctx.send_text()` か `InputText` を使ってください。これらは文字そのものを注入するので IME の
  影響を受けません。IME を閉じた状態でキーを送る必要があるなら、閉じたらそのままにして
  ください。ユーザーは `Kana` / 半角全角キーで元に戻せます。

単に切り替えるだけなら、キー名を使えば手間がかからず、両方の OS で動きます。`Eisu` は
IME オフ、`Kana` は IME オンで、macOS の同名のキーと Windows の `VK_IME_OFF` /
`VK_IME_ON` に届きます。

```python
kt["O-LCmd"] = "Eisu"        # tap left Cmd alone -> IME off
kt["O-RCmd"] = "Kana"        # tap right Cmd alone -> IME on
```

Windows にはさらに、物理的な JIS キー `Kanji`（半角/全角）、`Henkan`（変換）、`Muhenkan`
（無変換）があり、macOS にはこれらに相当するものがありません。これらは `keymap.platform`
で保護してください。

## クリップボード

```python
keymap.clipboard_history.max_items = 500
kt["Fn-V"] = ShowClipboardHistory()
```

`ShowClipboardHistory()` は、フォーカスのあるウィンドウの上にチューザー（選択ウィンドウ）の
ポップアップを開きます。入力すると絞り込み、`Up`/`Down` で選択、
**Enter で元のアプリに貼り付け**、**Shift-Enter はクリップボードに設定するだけ**、`Escape` で取り消しです。もう一度
ホットキーを押すと閉じます。

エントリはテキスト以上のものを保持します。リッチテキスト（RTF/HTML）、画像、コピーした
ファイルは、コピーしたときの形のまま貼り付けられます。**Option-Enter**（Windows では
Alt-Enter）は、エントリのテキストだけを書式なしで貼り付けます。Option-Shift-Enter は貼り付けずに
それを設定します。スプレッドシートのセルのように、テキストと一緒にそれ自身の画像も提供する
コピーは、テキストで一覧に表示され、プレビューには両方が表示されます。*(text only)* と表示された
エントリは、履歴がテキストしか保持できなかったコピーから来たものです。まだクリップボードにあるエントリを選ぶと、
コピーが提供していたものすべてとともに、手を加えずに貼り付けます。

リッチテキストと画像はセッションの間だけ保持されます（`max_session_data_size`、既定値は
64 MB。それを超えると古いものからテキストだけに戻ります）。テキストとファイルパスは再起動
しても保存されています。パスワードマネージャーが秘匿とマークした内容は決して記録されません。
そのような内容や、履歴が読めない形式のものがクリップボードにある間は、
**Current clipboard** の行が先頭に表示され、それを手を加えずに貼り付けます。

リストで **Shift-Delete** を押すと、選択したエントリが履歴と `clipboard.json` から削除されます
（Mac のキーボードでは fn-Shift-⌫）。クリップボード自体の内容はそのままです。独自のソースも
`remove()` をオーバーライドすれば同じ機能を提供できます。

絞り込みは複数語の AND による部分文字列一致に加えて Migemo にも対応しているので、`gijiroku`
と入力すれば、入力メソッドに切り替えなくても 議事録 を含むエントリが見つかります。

**ポップアップはキーボードフォーカスを取りません。** そのおかげで、元のアプリケーションは
アクティブなままで、コンソールはその場所にとどまり、今いるデスクトップから移動せず、遅延なしに
貼り付けられます。キー入力は代わりに Keyhac 自身のキーフックを通じてポップアップに届きます。
知っておくべき影響は 1 つです: **絞り込み欄では入力メソッドで変換できません。** 変換は OS の
キーボードフォーカスに従い、このウィンドウはそれを取らないからです。ローマ字で入力し、
Migemo に日本語を見つけさせてください。

**キー割り当てはポップアップの中でも機能します。** ポップアップが開いている間、キーテーブルは
ポップアップがフォーカスのあるウィンドウであるかのように照合され、割り当てが入力するものは
下にあるアプリではなくポップアップに送られます。すべての場所に適用されるテーブルはそこにも
適用されるので、グローバルなカーソルのリマップは追加の設定なしに選択を動かします。リマップ、
関数、マルチストロークのプレフィックス、ユーザーモディファイアはすべて機能します。
ポップアップだけにキーを割り当てるには、名前で照合します。ポップアップは自身をアプリ
`Keyhac`、タイトル `Chooser` として報告します（フォーカスパスは `/Keyhac/Chooser` で、開いて
いる間はコンソールに表示されます）。

```python
kt = keymap.define_keytable(focus_path_pattern="*")
kt["Ctrl-J"] = "Down"                   # also moves the popup's selection
kt["Ctrl-K"] = "Up"

kt_chooser = keymap.define_keytable(app="Keyhac", title="Chooser")
kt_chooser["Ctrl-G"] = "Escape"         # only in the popup
```

どのテーブルも割り当てていないキーは、ポップアップでの意味を保ちます（Up/Down、Enter、
Escape、絞り込み欄への入力）。それ以外については、下にあるアプリが自分の意味を保ちます。
`keymap.focus` とウィンドウのアクションは引き続きそのアプリを指し、ポップアップが受け取れない
キー出力（たとえば `Ctrl-S`）は、そこへ送られずに破棄されます。

```python
kt["Fn-Shift-V"] = ShowClipboardSnippets([
    ("📧", "me@example.com"),                          # (icon, text)
    ("📮", "Mailing address", "400 Broad St, ..."),    # (icon, label, text)
    ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),       # (icon, label, callable)
])

kt["Fn-Ctrl-V"] = ShowClipboardTools([
    ("🔄", "Quote", ShowClipboardTools.quote),
    ("🔄", "Upper case", str.upper),
    ("🔄", "Pretty JSON", my_pretty_json),             # str -> str
])
```

ツールは現在のクリップボードのテキストを変換します。組み込みのものは `quote`、`unindent`、
`to_half_width`、`to_full_width` で、`str -> str` の呼び出し可能なものなら何でも使えます。

履歴の動作は `keymap.clipboard_history` で設定できます: `max_items`（既定値 1000）、
`max_data_size`（取り込むテキストの最大サイズ、既定値 10 MB）、`max_persist_data_size`
（ディスクに書き込むエントリの最大サイズ、既定値 64 KB）、そして履歴をメモリだけに保持する
`persist = False` です。API はスクリプトからも使えます: `items()`、`get_current()`、
`set_current(text)`。

### 複数のソースを 1 つのウィンドウで

チューザーが 3 つならホットキーも 3 つで、ホットキーは貴重なものです。覚えておける数には
限りがあります。**ソース** は、代わりに 1 つのウィンドウに渡す値で、これを使うと何種類もの行が
1 つのキーと 1 つのインクリメンタルサーチを共有し、各行の右側にはどこから来たものかが表示
されます。

```python
kt["Fn-P"] = ShowCandidates([
    ClipboardHistorySource(),
    SnippetsSource(my_snippets),
    ClipboardToolsSource(my_tools),
])
```

リストを返すものなら何でもソースになれます。継承すべきクラスはありません。

```python
def open_windows():
    return [Candidate(icon="🪟", display=w.title, payload=w)
            for w in keymap.list_windows() if w.title]

kt["Fn-Shift-W"] = ShowCandidates(open_windows,
                                  on_chosen=lambda c, mod: c.payload.activate())
```

行は、どのソースが生成したかではなく、どれだけよく一致するかの順に並びます。行の先頭での
一致は単語の先頭での一致に勝ち、それは単語の途中での一致に勝ちます。同点なら、より前の、
あるいはより短い一致が勝ちます。クエリが空のときは判定するものがないので、ソース自身の順序が
そのまま使われます。クリップボード履歴なら新しいものが先です。

実際の処理が必要なソースは、リストを返す代わりに **yield** できます。最初の行はすぐに表示され、
残りはウィンドウが開いたあとに届きます。ウィンドウを閉じたりページを切り替えたりすると、
単に問い合わせが止まります。頻繁に yield し、ブロックしないでください。ソースはメインスレッド上で
少しずつ実行されるので、戻ってこないソースはキーボードを止めてしまいます。

共有のウィンドウで名前を持たせたり、選んだときの動作を独自に決めたりしたくなったら、クラスに
します。`name`、`candidates()`、`on_chosen()` を持つ `CandidateSource` です。Enter は選んだ行の
ソースが指定するものを実行するので、異なるソースの行は同じウィンドウの中で異なる意味を持て
ます。こちらは貼り付け、あちらはアクティブ化、別のものは押す、といった具合です。1 つの行で
それさえも上書きするには `Candidate(action=...)` を使います。

`ShowClipboardHistory()` とその 2 つの兄弟は、まさにこれらのソースを使ったプリセットです。

### ページ: 1 つのキーで複数のセット

ソースを名前付きのページにまとめると、1 つのキーですべてに到達できます。**Left と Right** で
ページ間を移動し、**クエリも一緒に移動します**。探しているものを入力し、それを打ち直さずに
別の場所で探せます。現在のページの名前は、絞り込み欄の右に表示されます。

```python
kt["Fn-P"] = ShowCandidates([
    ChooserPage("Run",   [keys, actions]),
    ChooserPage("Paste", [clipboard, snippets, tools]),
    ChooserPage("Click", [menus, controls, windows]),
])
```

**ページには、そこで Enter が何をするかで名前を付けてください。** そうすると、ページ間で
持ち運ぶクエリが名詞、ページが動詞になります。`mail` と入力すれば、Left/Right で、それを
実行するか、貼り付けるか、クリックするかを選べます。

**3 つを目安にし、いちばんよく使うものを真ん中に置いてください。** ウィンドウはそこで開くので、
そこへの到達には手間がかからず、両隣へは 1 回押すだけです。3 つは、見ずに覚えておける
ほぼ上限です。ページは折り返さずに端で止まるので、左は常に左です。その方向に何もないときは、
名前の横の `‹` や `›` が消えます。

もっと欲しい場合は、別のキーに別の `ShowCandidates` を割り当ててください。ここではキーが
貴重なものですが、それをどう使うかはあなた次第です。

### `@` で 1 つのソースに絞り込む

どのページにいても、`@` とソース名（`@Clip`、`@Menu`、`@Key`）を入力すると、そのソースの
行だけが表示されます。`Tab` は入力した内容を確実なところまで補完し、名前が 1 つに絞られたら
スペースを追加するので、クエリの残りをそのまま続けて入力できます。

名前は各行の横にすでに表示されているものなので、覚えることは何もありません。このページに
ない名前は、単に何にも一致しません。クエリの残りは引き続き絞り込みに使われます:
`@Menu save` は、メニューコマンドの中から `save` を探します。

だからこそ 3 ページで足りるのです。ページは *それで何をするか* を表し、`@` は
*そこにあるもののうちどれか* を表します。

組み込みのソースは `ClipboardHistorySource`、`SnippetsSource`、`ClipboardToolsSource`、
`MenuItemsSource`（前面のアプリケーションのメニューにあるすべてのコマンドとそのショートカット）、
`KeyBindingsSource`（この場所で有効なすべての割り当て。Enter でそれを実行します。リストから
実行できる割り当てには専用のキーが要りません）、`ActionsSource`（`~/.keyhac/extensions/` の下の
すべての `ThreadedAction`。何かに割り当てなくても開始できます）、そして
`WindowControlsSource`（前面のウィンドウでクリックできるすべてのものを名前で）です。

最後の 2 つは呼び出しのたびに前面のアプリケーションを読み取るので、統合したページに入れる
のではなく、それぞれに専用の `ChooserPage` を与えてください。

`MenuItemsSource` は **macOS 専用** です。macOS ではメニューバーは OS レベルの部品であり、
アプリケーションごとに 1 つ、常に画面の上端にあり、閉じている間も全体を読み取れます。だからこそ
すべてのコマンドの平らなリストが作れるのです。Windows のメニューはウィンドウに属し、そもそも
存在しないこともあり、開いたときに初めて中身が入ります。そのため、このソースはメニューバーを
見つけられず、何も返しません。Windows ではメニューの最上位の項目はウィンドウの UI 要素であり、
`WindowControlsSource` がほかのクリックできるものと一緒にそれらを列挙します。

**各ソースは一度だけ作り、ページ間で共有してください。** ソースはウィンドウごとに一度だけ
読み取られ、オブジェクトに対して記憶されます。そのため、すべてを集めたページとそれ専用の
ページの両方に現れる `MenuItemsSource` は、同じインスタンスであれば、メニューバーを一度だけ
たどります。別々に作った 2 つのソースは 2 つのソースであり、それらが異なるときにはそれが
望ましい動作です。

```python
menus = MenuItemsSource()
kt["Fn-P"] = ShowCandidates([
    ChooserPage("All",   [clipboard, menus]),
    ChooserPage("Menus", [menus]),
])
```

ページは、コストの高いソースを手頃に保つ方法でもあります。実際の処理が必要なソース
（ウィンドウのコントロールをたどる、サーバーに問い合わせるなど）は、開くページに含まれるたびに
その処理のコストがかかります。専用のページに入れておけば、求めたときにだけそのコストを
払うことになります。

### プレビュー

選択がある行にとどまると、チューザーの横の **プレビューウィンドウ** に、1 行では表せないものが
表示されます。サイズと形式を含むテキスト全体、画像、コピーしたファイル、アクションの説明です。
クリップボードのエントリ、定型文、クリップボードツール（選ぶ前の変換結果）、アクションに
プレビューがあります。

独自のソースは、`preview()` をオーバーライドしてプレビューを提供します。

```python
class Branches(CandidateSource):
    name = "Branch"
    preview_background = True     # it runs git, not Keyhac: a worker may run it

    def preview(self, candidate):
        log = subprocess.run(["git", "log", "-5", "--oneline", candidate.payload],
                             capture_output=True, text=True).stdout
        return CandidatePreview(title=candidate.payload, text=log)
```

`CandidatePreview` は `title`、`image`（エンコード済みのバイト列またはパス）、`text`、
`fields`（`(label, value)` の組）を受け取ります。ラベルですべて言い尽くしている行では None を
返してください。`preview()` は選択がとどまってから問い合わせられるので、実際の処理をしても
構いません。ただし、メインスレッド上ではその処理がキーボードを止めるので、遅いものは
`preview_background = True` を設定します。`background` と同じ規則で、触れてよいのは
Keyhac の外の世界だけです。

### ウィンドウが開く位置

既定では、チューザーは **キャレットの下に** 開き、フォーカスのあるコントロール、さらに
フォーカスのあるウィンドウの中央へとフォールバックします。各段階は、前の段階が読み取れない
とき、*または信用できないとき* に次へ進みます。かなり多くのアプリケーションが、キャレットの
問い合わせにキャレットのない位置の矩形を返すので、そこに置いたポップアップは誰も見ていない
ところに現れてしまいます。

ウィンドウの中の場所ではなくウィンドウそのものに作用するチューザーには、`anchor="window"` を
渡します。ウィンドウスイッチャーがキャレットの横に開く理由はありません。

```python
kt["Fn-O"] = ShowCandidates([ChooserPage("Windows", [OpenWindows()])],
                            anchor="window")
```

この点はアプリケーションによって大きく異なるので、**推測せずに調べてください。**
`ReportCaretAnchor()` をキーに割り当て、対象のアプリケーションの中で押します。

```python
kt["Fn-Ctrl-C"] = ReportCaretAnchor()
```

コンソールには、Keyhac がそこで読み取れるキャレット（または読み取れない理由）が INFO で
出力されます。事前にデバッグログを有効にする必要はありません。そして、見つけた場所にバルーンが
表示されます。`make caret-probe` もターミナルから同じことをしますが、実行中に前面にあるのは
ターミナルなので、通常はキーのほうが適しています。

### チューザーをクラスとして書く

独自のチューザーは `ChooserAction` から派生させることもできます。
`list_items() -> [(icon, label, ...)]` と `on_chosen(item, modifier_flags)` を実装すれば、
開く / 絞り込む流れは継承されます。3 つのクラス属性で調整できます: `matcher`（既定は
部分文字列 + Migemo。`*` と `?` には `WildcardMatcher()`）、`activates`（既定は `False`。
絞り込み欄で本当に入力メソッドが必要な場合だけ `True` にします。その代わりに下にある
アプリケーションのフォーカスが失われます）、そして `anchor`（上記）です。

## ウィンドウ、画面、アプリケーション

```python
kt["O-RCmd"] = ActivateApplication(app="Terminal|ターミナル",  # go to it, then
                                   launch="Terminal.app")     # window by window
kt["Fn-1"] = ActivateWindow(app="code|Visual Studio Code")   # bring forward
kt["Fn-T"] = LaunchApplication("Terminal.app")               # launch, every press

kt["Fn-Ctrl-Left"] = MoveWindow(direction="left", distance=20)
kt["Fn-Alt-Left"]  = MoveWindow(direction="left", distance=9999,
                                window_edge=True, screen_edge=True)
kt["Fn-Ctrl-J"] = SnapWindow("left")          # tile: left/right/top/bottom/full
kt["Fn-F"]      = SnapWindow("full")          # ratio=2/3 etc. picks the split
```

アプリケーションに到達するアクションは 3 つあり、「そこへ連れて行く」のどこまでを行うかが
異なります。`ActivateApplication` はそのすべてを行います。押すとアプリケーションの最前面の
ウィンドウが前に出て、*すでに* 前面にあるときに押すと次のウィンドウに移り（逆にたどるには
`reverse=True` で 2 つ目のキーを割り当てます）、実行されていないときは `launch=` で起動します。
`ActivateWindow` は、その 2 つを除いた同じものです。1 回押すと最前面のウィンドウ、起動は
しません。`LaunchApplication` は名前を OS に渡すだけで、Windows では押すたびに新しい
ウィンドウが開くことになります。

ローテーションは状態を持ちません。どのウィンドウが現在のものかは毎回 Z オーダーから読み取られ、
たどる順序はウィンドウの画面上の位置なので、設定の再読み込みや、ウィンドウが開いたり、
閉じたり、別の場所へドラッグされたりしても崩れません。照合には *OS* が報告する名前を使って
ください。macOS はこれをローカライズするので、日本語のシステムでは Terminal は
`ターミナル` になります。コンソールのフォーカス欄に現在の値が表示されます。

`MoveWindow` は `distance` ピクセル（既定値 10）ずつ動かすか、`window_edge=`/`screen_edge=`
を指定すると、ほかのウィンドウの端 / 画面の端に当たるまで移動します（すでに画面の端にあるときは
次のモニターへ移ります）。既定でオンなのは `screen_edge` だけです。`SnapWindow` は画面の
*作業領域* 内（メニューバー、Dock、タスクバーは覆いません）にタイル配置し、`ratio=` で指定しない
限り画面の半分を占めます。ウィンドウを OS のスナップに任せずに自分で配置します。ウィンドウは
Windows のスナップが置くのと同じ位置に収まりますが、スナップアシストは表示されず、スナップ
グループも作られません。

独自のロジックには、完全に移植可能な `Window` オブジェクトを使えます。

```python
window = keymap.get_active_window()           # or find_window(app=, title=,
x, y, w, h = window.get_frame()               #    class_name=), list_windows()
                                              # the visible frame: Windows'
                                              # invisible resize border is not
                                              # part of it, on either accessor
window.set_frame(x + 100, y)
window.activate()
window.minimize(); window.maximize()          # maximize: Window > Fill on macOS
window.enter_full_screen()                    # macOS only
window.restore()                              # the way back out of all three
window.is_minimized(); window.is_maximized(); window.is_full_screen()
window.title; window.app_name; window.pid; window.class_name  # class_name: Windows
```

`find_window` は `define_keytable` とまったく同じように照合します。ワイルドカード、`|` による
選択、大文字と小文字の区別なし、`.exe` は省略可能です。画面の配置情報は `keymap` にあります:
`screen_frames()`（画面全体、プライマリが先頭）、`screen_work_frames()`（メニューバー / Dock /
タスクバーを除いたもの）、`window_frames()`（画面上のすべての通常のウィンドウ）。各フレームは
共通の座標空間での `(x, y, w, h)` のタプルです。

**スレッドの約束事**: 守るべきものはありません。`Window` オブジェクト、要素、`keymap.ui`、
配置情報の問い合わせは、すべて `ThreadedAction.run()` から使えます。これらはほかの
アプリケーションと直接やり取りするので、遅いアプリケーションや応答しないアプリケーションが
止めるのはそのアクションであり、キーボードではありません。メインスレッド以外では実行できない
少数のプラットフォーム呼び出しは、自分からメインスレッドに移ります。クリップボード、
`screen_work_frames()`、IME の呼び出しです。

それを可能にしているのは 1 つの規則です:
**Keyhac 自身のウィンドウは、Keyhac の API からは存在しないものとして扱われます。** ウィンドウの検索ではそれらを飛ばし、要素の API は決して
それらを返さず、Keyhac のウィンドウが前面にある間 `focus.element` は `None` です。（キー
テーブルはそこでも `app=` などで照合されます。）スクリーンリーダーには影響しません。

## Focus オブジェクト

`keymap.focus`（およびすべての `custom_condition_func` の引数）は、現在のキーボード
フォーカスのスナップショットです: `app_name`、`pid`、`window_title`、`class_name`（Windows
のみ）、`path`（フォーカスパスの文字列）、`element`、`native`。

`focus.element` はフォーカスされた *意味上の* 要素です。macOS では AX 要素、Windows では
UI Automation 要素です。どちらも同じ形をしています:
`get_attribute_names()`、`get_attribute_value(name)`、`get_action_names()`、
`perform_action(name)`、`parent()`。ただし、それぞれ **自分の OS の語彙** を使います。

| | macOS (AX) | Windows (UI Automation) |
|---|---|---|
| ロール | `AXRole` | `ControlType`（`"Edit"`、`"Window"`、…） |
| ラベル | `AXTitle` | `Name` |
| テキストの値 | `AXValue` | `Value` |
| 選択範囲 | `AXSelectedText` | `SelectedText` |
| 押す | `perform_action("AXPress")` | `perform_action("Invoke")` |

そのため、要素レベルのコードは `keymap.platform` で分岐します。失敗のしかたは穏やかです。
サポートされない属性は `None` として読み取られ、未知のアクションはログを出して `False` を
返します。`focus.native` はプラットフォームのより強力なオブジェクトで、macOS では同じ AX 要素、
Windows では HWND のラッパーです。

### ほかのアプリケーションを操作する: アクション API

ほかのアプリケーションの UI の読み書き（要素ツリーの検索、画面の変化の待機、入力欄への入力）は、
`keymap.ui`（`ThreadedAction` の中では `self.ui`）を通じて使う別の API です。

```python
class Extract(ThreadedAction):
    def run(self):
        window = self.ui.window(app="Safari")
        window.wait_for(role="AXTable", message="the results to load")
        for row in window.find_all(role="AXRow"):
            print([cell.all_text for cell in row.children])
```

これは意図的に独自の名前空間とメソッド形式になっています。設定はキーを割り当て、アクションは
他人の UI を操作するものであり、インポートできるのは `UINode`、`WaitTimeout`、`FillFailed`
だけです。全体については **[Action API](action-api.md)** を、書き方については
`keyhac/skills/keyhac/`（`references/actions.md`）を参照してください。

**`UINode` はスナップショットです。** 要素を読み取った時点の状態を記録したもので、画面が
先に進んでもそれに気づきません。`reread()` は意図的に 1 つを更新し、`StaleElement` は保持して
いるノードが指していたものより長く残ってしまったことを知らせます。エンドポイントを有効に
する方法と、それで到達できる範囲については [AI Integration](ai-integration.ja.md) を参照して
ください。

## キーボードマクロ

```python
kt["Fn-OpenBracket"]  = ToggleRecordingKeys()     # record on/off
kt["Fn-CloseBracket"] = PlaybackRecordedKeys()    # replay
```

別々のキーにしたい場合は `StartRecordingKeys()` / `StopRecordingKeys()` もあります。
再生したキーはキーマップを再び通るので、記録した割り当ては再生時に展開されます。
バッファーは `keymap.replay_buffer` です。

## バックグラウンド処理: ThreadedAction

遅い処理（ネットワーク、サブプロセス、スリープ、重い計算）はその場で実行してはいけません。
関数はキーボードフックの制限時間内で実行されるからです。代わりに `ThreadedAction` を継承します。

```python
class Fetch(ThreadedAction):
    def starting(self):          # main thread, before run
        logger.info("fetching...")
    def run(self):               # worker thread - the slow part
        return do_network_call()
    def finished(self, result):  # main thread, after run
        logger.info(f"got {result}")

kt["Fn-G"] = Fetch()
```

`starting()` と `finished()` はメインスレッドで実行されるので、軽く保つべきです。`run()` は
ワーカーで実行され、アクションに必要なことはすべてそこで行えます（前述のスレッドの約束事を
参照）。

プールは **すべてのスレッド化されたアクションが共有する単一のワーカー** なので、スリープしたり
ループしたりする `run()` は、戻るまでほかのすべてのアクションを遅らせます。長い待機は短く
保ち、何かを待つためにワーカーを占有するよりも `keymap.call_on_main_thread(func)`（スレッド
セーフで、どこからでもメインスレッドに到達するためのサポートされた方法）を使ってください。

### アクションを一覧に載せるのもこれ

`ThreadedAction` のサブクラスを `~/.keyhac/extensions/` の下のファイルに置くと、
**キーなしで開始** できるようになります。候補ウィンドウから名前で、また MCP エンドポイントを
通じて AI アシスタントから開始できます。サブディレクトリも対象になるので、
`extensions/mine/extract.py` は `mine.extract.Extract` として指定されます。`_` で始まる名前は
ヘルパーとして扱われ、一覧には出ません。

`ThreadedAction` を継承することが、それらの一覧に載る条件です。`__call__` を定義しているだけの
クラスもキーには問題なく割り当てられますが、意図的に一覧には *載りません*。メインスレッドは
キーボードフックとすべてのウィンドウを処理しているので、その行がメインスレッドをブロック
しうる一覧は、キーボードを固まらせうる一覧だからです。速いアクションを一覧に載せたい場合でも
継承し、`run()` を短くしてください。処理が小さければワーカースレッドのコストはかかりません。

## バルーン

```python
keymap.pop_balloon("hello", "Keyhac is running", 2.0)   # name, text, timeout
keymap.close_balloon("hello")
```

バルーンは枠のない小さなツールチップで、**入力している場所** に開きます。キャレットの下、
次にフォーカスのあるコントロール、次にフォーカスのあるウィンドウのタイトルバーへと
フォールバックし、そのどれもないときに限ってメイン画面の右上隅に開きます。これはチューザーが
たどるのと同じ順序で、[ウィンドウが開く位置](#ウィンドウが開く位置)の内容はすべて
ここにも当てはまります。キャレットの問い合わせにキャレットのない位置の矩形を返す
アプリケーションについても同様です。そういうアプリケーションがどう振る舞うかは
`ReportCaretAnchor()` で調べられます。

マルチストロークのテーブルは、自動的に独自のバルーンを表示します。

`anchor=` を指定すると、別の場所に表示します。

```python
keymap.pop_balloon("win", "acting on this window", 2.0, anchor="window")
keymap.pop_balloon("mode", "insert mode", 2.0, anchor="corner")
```

`"window"` は、フォーカスのあるウィンドウのタイトルバーに直接表示します。ウィンドウの中の
場所ではなく、ウィンドウについてのバルーンに使います。`"corner"` はメイン画面の右上で、
場所とは関係のないバルーンに使います。プラットフォームに何も問い合わせない唯一の配置でも
あります。

バルーンの表示は、以前からそうだったように、メインスレッドの処理です。ウィンドウを描画し、
さらにキャレットを見つけるために UI Automation（Windows）や Accessibility（macOS）の呼び出しを
行うからです。キー割り当てから呼び出すか、`ThreadedAction` のワーカーからなら
`keymap.call_on_main_thread` を介して呼び出してください。

`--no-ui` モードではこれらの属性は存在しません。設定をヘッドレスで動かす必要がある場合は
`getattr(keymap, "pop_balloon", None)` で保護してください。

## ログとコンソール

`print()` と `getLogger(name)` は、どちらもコンソールウィンドウに出力されます。

```python
logger = getLogger("Config")
logger.info("loaded")
```

コンソールのドロップダウンでレベルを絞り込めます。コマンドラインの `-d`/`--debug` で、起動時
からデバッグログが有効になります。コンソールには、最後のキーイベントと現在のフォーカスパスも
表示されます。新しい割り当てを書くときに必要な 2 つの情報です。

## ランタイム API の概要

| API | 説明 |
|---|---|
| `keymap.platform` | `"windows"` / `"mac"` |
| `keymap.define_keytable(...)` | キーテーブル + フォーカス条件 |
| `keymap.replace_key(src, dst)` / `define_modifier(key, mod)` | エンジン前段のリマップ / ユーザーモディファイア |
| `keymap.get_input_context()` | 仮想入力のまとめ送り。スレッドセーフ |
| `keymap.focus` | 現在の `Focus` スナップショット |
| `keymap.get_active_window()` / `list_windows()` / `find_window(...)` | 移植可能な `Window` オブジェクト（Keyhac 自身のウィンドウは除外） |
| `keymap.screen_frames()` / `screen_work_frames()` / `window_frames()` | 画面 / ウィンドウの配置情報 |
| `keymap.get_ime_status()` / `set_ime_status(on)` | 入力フォーカスの IME のオン / オフ |
| `keymap.clipboard_history` | 設定 + `items()` / `get_current()` / `set_current()` |
| `keymap.editor` / `edit_config()` / `reload_config()` | 設定のライフサイクル（トレイメニューが使用） |
| `keymap.replay_buffer` | 記録アクションの背後にあるマクロバッファー |
| `keymap.pop_balloon(name, text, timeout, anchor=…)` / `close_balloon(name)` | バルーン（UI モードのみ）。`anchor` で指定しない限りキャレットの位置に開く |
| `InputText(s)` | 文字列をそのまま入力 |
| `MoveWindow(...)` / `SnapWindow(...)` / `ActivateApplication(...)` / `ActivateWindow(...)` / `LaunchApplication(...)` | ウィンドウとアプリのアクション |
| `MouseMove` / `MouseButtonDown/Up/Click` / `MouseWheel` / `MouseHorizontalWheel` | マウス出力のアクション |
| `ShowClipboardHistory()` / `ShowClipboardSnippets(...)` / `ShowClipboardTools(...)` / `DateTimeSnippet(fmt)` | クリップボード UI のアクション |
| `ChooserAction` | 独自のチューザーポップアップの基底クラス |
| `ThreadedAction` | バックグラウンド処理 |
| `Start/Stop/Toggle/PlaybackRecordedKeys()` | キーボードマクロ |
| `getLogger(name)` | コンソールへのログ出力 |

これらすべての正確なシグネチャ、既定値、引数ごとの説明は
[API リファレンス](config-api.md)にあります。
