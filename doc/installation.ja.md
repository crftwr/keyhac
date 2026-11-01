# インストール

[English](installation.md) \| 日本語

## 動作環境

- **macOS 12 以降**、または
- **Windows 10 / 11（64 ビット）**。

Keyhac はすべてお使いのマシン上で動きます。インターネットには一切接続しません。更新の確認もテレメトリもありません。
唯一の例外は、自分で行う操作です。トレイメニューの
**Advanced > Sync Python Packages** は、`requirements.txt` に列挙した追加ライブラリをダウンロードします
（[設定](configuration.ja.md#追加の-python-ライブラリ)を参照）。
自分で選ばない限り、ネットワークには何も送られません。

## macOS

1. [Releases](https://github.com/crftwr/keyhac/releases) から `Keyhac-<version>-macos.dmg` を
   ダウンロードして開き、**Keyhac.app** を **Applications** にドラッグします。
2. Keyhac を起動します。メニューバーアプリなので、メニューバーのキーキャップのアイコンを探してください。
   Dock にアイコンはありません。
3. 初回起動時、macOS が **アクセシビリティ権限** を求めます
   （System Settings → Privacy & Security → Accessibility。macOS 12 では System
   Preferences → Security & Privacy → Privacy → Accessibility）。Keyhac のキーボードフックは
   これがないと動きません。権限はアプリのバンドルに結び付いているので、更新後も保持されます。

ログイン時に Keyhac を起動するには、System Settings → General → Login Items に
Keyhac.app を追加します。

### Homebrew

```
brew install --cask crftwr/tap/keyhac
```

これは同じ DMG を Applications にインストールするので、上の手順 2 と 3 はそのまま必要です。
Keyhac は更新を確認しないので、`brew upgrade --cask keyhac` を使うと手軽に最新の状態を保てます。
これはアプリを置き換える前に実行中の Keyhac を終了させるので、その後で Keyhac を起動し直してください。

- DMG をすでに手動でインストールしている場合は、`--adopt` を付けてください。Homebrew が上書きを拒否せず、
  そのコピーを管理下に置きます。
- `brew uninstall --cask keyhac` でアプリが削除されます。`~/.keyhac/` には、`--zap` を付けても
  一切手を付けません。

## Windows

### Microsoft Store（推奨）

[Microsoft Store](https://apps.microsoft.com/detail/9P8H1PG6PRHH) から **Keyhac** を
インストールするか、ターミナルから次を実行します。

```
winget install --id 9P8H1PG6PRHH --source msstore
```

Store 版のパッケージは自動で更新され、SmartScreen の警告も出ず、
**既定でログイン時に起動** するよう設定されます。この設定は
Settings → Apps → Startup で切り替えます。

### zip パッケージ

1. [Releases](https://github.com/crftwr/keyhac/releases) から `Keyhac-<version>-win64.zip` を
   ダウンロードし、任意の場所に展開します
   （たとえば `C:\Program Files\Keyhac` やホームディレクトリ内のフォルダー）。
2. `Keyhac.exe` を実行します。
3. SmartScreen が認識されないアプリについて警告した場合は、"More info" → "Run anyway" を選びます。

zip 版をログイン時に起動するには、`Keyhac.exe` へのショートカットを
スタートアップフォルダー（`Win-R` → `shell:startup`）に置きます。

### 起動後

Keyhac はタスクトレイの通知領域にキーキャップのアイコンとして表示されます。初回起動時には
コンソールウィンドウが開きます。

Keyhac 自身が管理者権限で動いていない限り、**管理者権限で動く**（昇格された）ウィンドウへの
キーボード入力は Keyhac から見ることも変更することもできません。これは Windows のセキュリティ境界です。
管理者権限のアプリの中でもキー割り当てが必要な場合に限り、Keyhac を管理者権限で実行してください。

## 実行

トレイ／メニューバーのアイコンが操作の中心です。

| メニュー項目 | 動作 |
|---|---|
| Open Console | コンソールウィンドウを表示します（ログ、フックの切り替え、ログレベル、最後のキーとフォーカスパスのインスペクター） |
| Edit Config | `~/.keyhac/config.py` をエディターで開きます（変更できます。[configuration.ja.md](configuration.ja.md) を参照） |
| Reload Config | 設定を再実行します。エラーがあると直前のキーマップが有効なまま残り、エラーはコンソールに表示されます |
| Keyboard Hook | フックのオン／オフを切り替えます（オフ = すべてのキーがそのまま通されます） |
| Quit Keyhac | フックを解除して終了します |

コンソールウィンドウを閉じても隠れるだけで、Keyhac はトレイ／メニューバーで動き続けます。
コンソールの表示／非表示の状態は再起動後も記憶されます。

Keyhac は単一インスタンスで動きます。実行中にもう一度起動すると、実行中のインスタンスのコンソールが
再表示されるだけです。

## データファイル

どちらの OS でも、すべて `~/.keyhac/` の下に置かれます。

| ファイル | 内容 |
|---|---|
| `config.py` | 設定（初回起動時にテンプレートから作成されます） |
| `extensions/` | `sys.path` に含まれます。自作の Python モジュールをここに置き、config.py から import します |
| `clipboard.json` | 永続化されたクリップボード履歴 |
| `settings.json` | アプリの状態（コンソールの表示状態など） |
| `performance/` | 取得したサンプリングデータ。1 回の取得ごとに 1 フォルダ（下記参照） |

`--config PATH` を付けて実行すると、`clipboard.json` と `settings.json` はその設定ファイルの隣に置かれます。
サンドボックス化した環境や実験用の環境に便利です。

**Keyhac が重い・忙しいとき**（何もしていないのに CPU 使用率が高い、キー入力が遅れるなど）は、
その状態のうちにトレイ／メニューバーのメニューで **Performance > Capture Sampling Data** を選びます。
Keyhac は 10 秒間、メインスレッドが何に時間を使っているかを記録し、
`~/.keyhac/performance/` の下に書き出したフォルダを開きます。
そのフォルダを [GitHub の Issue](https://github.com/crftwr/keyhac/issues) に添付してください。
最初に読むべきなのは `summary.txt` です。
macOS では、ネイティブのスタック（システムの `sample` ツールによる `native-sample.txt`）も同じフォルダに入ります。

### ポータブルモード (Windows)

`Keyhac.exe` の隣に `config.py` を置くと、そのディレクトリがデータディレクトリになります。
設定、クリップボード履歴、各種設定はすべて実行ファイルの隣に置かれ、ユーザープロファイルには何も書き込まれません。
これは Keyhac 1.x にあったものと同じポータブルモードで、有効にする方法も同じです。ファイルがあるかどうかだけで
切り替わるので、削除すれば `~/.keyhac` に戻ります。

USB メモリで Keyhac を使う場合や、独立した複数の環境を並べて使う場合に利用してください。既存の
`~/.keyhac/config.py` か、[テンプレート](../keyhac/_config.py)のコピーから始めます。

ポータブルなインストールでも、2 つのものは外部に置かれます。コンソールウィンドウの記憶された位置
(`HKCU\Software\PuiKit\FrameAutosave`) と、ウィンドウが開く前に Keyhac が失敗した場合に
`~/.keyhac` に書き込まれる `keyhac-error.log` です。どちらも設定には影響しません。

ポータブルモードは Windows 専用です。macOS の `.app` は署名されたバンドルで Gatekeeper が
再検証するため、Keyhac はその中に書き込めません。macOS では `--config PATH` を使ってください。

**Keyhac for Windows (1.x) のユーザーへの注意**: データディレクトリは
`%APPDATA%\Keyhac` から `~/.keyhac` に移りました。初回起動時、Keyhac 2 はそこにある 1.x の
`config.py` を見つけると、コピーするかどうかを尋ねます。その後も API の移行作業は必要です。
[migration-from-keyhac-win.ja.md](migration-from-keyhac-win.ja.md) を参照してください。

## プライバシーに関する注意

- キーボードフックは、その仕組み上すべてのキー入力を見ます。それがキーボードカスタマイズツールというものです。
  Keyhac はキー入力をプロセス内で処理し、1 つも保存しません。ただし、自分で制御できる例外が 2 つあります。
  キーボードマクロのバッファー（記録中はメモリに保持されます）と、クリップボード履歴です。
- **クリップボード履歴はディスクに書き込まれます**（`~/.keyhac/clipboard.json`: テキストは
  1 エントリあたり最大 64 KB、およびコピーしたファイルのパス。画像と書式はメモリにのみ保持されます）。
  パスワードマネージャーが秘匿とマークした内容は記録されません。マークしない場所からコピーしたパスワードについては、
  config.py で永続化を無効にすることを検討してください。

  ```python
  keymap.clipboard_history.persist = False   # keep history in memory only
  ```

  または件数に上限を設けます: `keymap.clipboard_history.max_items = 100`。
- macOS では、アクセシビリティ権限によって Keyhac は UI 要素の詳細（ウィンドウタイトル、フォーカスのあるコントロール）も
  読み取れます。これはフォーカス条件と `focus.element` API にだけ使われ、それ以外には使われません。

## アンインストール

- **macOS**: Keyhac を終了し、Keyhac.app と `~/.keyhac` を削除して、
  System Settings → Privacy & Security → Accessibility にある Accessibility のエントリを削除します
  （macOS 12 では System Preferences → Security & Privacy → Privacy）。
- **Windows (Microsoft Store)**: Keyhac を終了し、Settings → Apps → Installed apps から
  アンインストールして（または `winget uninstall Keyhac`）、`~/.keyhac` を削除します。
- **Windows (zip)**: Keyhac を終了し、展開したフォルダーと `~/.keyhac` を削除して、
  スタートアップのショートカットを作成していた場合はそれも削除します。
