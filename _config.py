import sys
import os
import datetime
import subprocess

from keyhac import *

def configure(keymap):

    # --------------------------------------------------------------------
    # config.py編集用のテキストエディタの設定

    # プログラムのファイルパスを設定 (単純な使用方法)
    if 1:
        keymap.editor = "TextEdit"
        #keymap.editor = "Sublime Text 2"

    # 呼び出し可能オブジェクトを設定 (高度な使用方法)
    if 0:
        def editor(path):
            subprocess.call([ "open", "-a", "TextEdit", path ])
        keymap.editor = editor


    # --------------------------------------------------------------------
    # 表示のカスタマイズ

    # フォントの設定
    keymap.setFont( "Osaka-Mono", 16 )

    # テーマの設定
    keymap.setTheme("black")


    # --------------------------------------------------------------------

    # キーの置き換えと、モディファイアキーの追加
    if 1:
        # 右Shiftキーを、BackSpaceに置き換える
        keymap.replaceKey( "RShift", "Back" )
        
        # 右Altキーを、仮想キーコード 255 番に置き換える
        keymap.replaceKey( "RAlt", 255 )
        
        # 仮想キーコード255番を、ユーザ定義のモディファイアキーとして追加する
        keymap.defineModifier( 255, "User0" )


    # どのウインドウにフォーカスがあっても効くキーマップ
    keymap_global = keymap.defineWindowKeymap()


    # Fn-A : 任意の呼び出し可能オブジェクトをキーに割り当てる例
    if 1:
        def command_HelloWorld():
            print("Hello World!")
            
        keymap_global["Fn-A"] = command_HelloWorld


    # キーボードでウインドウの移動
    if 1:
        # Ctrl-Alt-↑↓←→ : 10pixel単位のウインドウの移動
        keymap_global[ "Ctrl-Alt-Left"  ] = keymap.MoveWindowCommand( -10, 0 )
        keymap_global[ "Ctrl-Alt-Right" ] = keymap.MoveWindowCommand( +10, 0 )
        keymap_global[ "Ctrl-Alt-Up"    ] = keymap.MoveWindowCommand( 0, -10 )
        keymap_global[ "Ctrl-Alt-Down"  ] = keymap.MoveWindowCommand( 0, +10 )

        # Ctrl-Alt-Shift-↑↓←→ : 1pixel単位のウインドウの移動
        keymap_global[ "Ctrl-Alt-Shift-Left"  ] = keymap.MoveWindowCommand( -1, 0 )
        keymap_global[ "Ctrl-Alt-Shift-Right" ] = keymap.MoveWindowCommand( +1, 0 )
        keymap_global[ "Ctrl-Alt-Shift-Up"    ] = keymap.MoveWindowCommand( 0, -1 )
        keymap_global[ "Ctrl-Alt-Shift-Down"  ] = keymap.MoveWindowCommand( 0, +1 )

        # Ctrl-Alt-Cmd-↑↓←→ : 画面の端まで移動
        keymap_global[ "Ctrl-Alt-Cmd-Left"  ] = keymap.MoveWindowToMonitorEdgeCommand(0)
        keymap_global[ "Ctrl-Alt-Cmd-Right" ] = keymap.MoveWindowToMonitorEdgeCommand(2)
        keymap_global[ "Ctrl-Alt-Cmd-Up"    ] = keymap.MoveWindowToMonitorEdgeCommand(1)
        keymap_global[ "Ctrl-Alt-Cmd-Down"  ] = keymap.MoveWindowToMonitorEdgeCommand(3)


    # ワンショットモディファイアの例
    # 右Commandキーの単押しで、IME切り替え
    if 1:
        keymap_global[ "O-RCmd" ] = "Ctrl-Space"


    # キーボードマクロ
    if 1:
        keymap_global[ "Fn-0" ] = keymap.command_RecordToggle
        keymap_global[ "Fn-1" ] = keymap.command_RecordStart
        keymap_global[ "Fn-2" ] = keymap.command_RecordStop
        keymap_global[ "Fn-3" ] = keymap.command_RecordPlay
        keymap_global[ "Fn-4" ] = keymap.command_RecordClear


    # TextEdit のキーカスタマイズ
    if 1:
        keymap_textedit = keymap.defineWindowKeymap( app_name="com.apple.TextEdit" )

        keymap_textedit[ "Cmd-R" ] = "Alt-Cmd-F"                   # 置換
        keymap_textedit[ "Cmd-L" ] = "Cmd-Right", "Cmd-Shift-Left" # 行選択


    # TextEditを Emacs 風にカスタマイズする (マルチストロークの例)
    if 1:

        # Ctrl-X を マルチストロークの1段目として登録
        keymap_textedit[ "Ctrl-X" ] = keymap.defineMultiStrokeKeymap("Ctrl-X")

        keymap_textedit[ "Ctrl-P" ] = "Up"                  # カーソル上
        keymap_textedit[ "Ctrl-N" ] = "Down"                # カーソル下
        keymap_textedit[ "Ctrl-F" ] = "Right"               # カーソル右
        keymap_textedit[ "Ctrl-B" ] = "Left"                # カーソル左
        keymap_textedit[ "Ctrl-A" ] = "Home"                # 行の先頭
        keymap_textedit[ "Ctrl-E" ] = "End"                 # 行の末尾
        keymap_textedit[ "Alt-F" ] = "Alt-Right"            # 単語右
        keymap_textedit[ "Alt-B" ] = "Alt-Left"             # 単語左
        keymap_textedit[ "Ctrl-V" ] = "PageDown"            # ページ下
        keymap_textedit[ "Alt-V" ] = "PageUp"               # ページ上
        keymap_textedit[ "Ctrl-X" ][ "Ctrl-F" ] = "Cmd-O"   # ファイルを開く
        keymap_textedit[ "Ctrl-X" ][ "Ctrl-S" ] = "Cmd-S"   # 保存
        keymap_textedit[ "Ctrl-X" ][ "U" ] = "Cmd-Z"        # アンドゥ
        keymap_textedit[ "Ctrl-S" ] = "Cmd-F"               # 検索
        keymap_textedit[ "Ctrl-X" ][ "H" ] = "Cmd-A"        # 全て選択
        keymap_textedit[ "Ctrl-W" ] = "Cmd-X"               # 切り取り
        keymap_textedit[ "Alt-W" ] = "Cmd-C"                # コピー
        keymap_textedit[ "Ctrl-Y" ] = "Cmd-V"               # 貼り付け
        keymap_textedit[ "Ctrl-X" ][ "Ctrl-C" ] = "Cmd-W"   # 終了


    # ウインドウのアクティブ化
    if 1:
        # Fn-T : ターミナルのアクティブ化
        keymap_global[ "Fn-T" ] = keymap.ActivateApplicationCommand( "com.apple.Terminal" )


    # アプリや子プロセスの起動
    if 1:

        # Fn-E : TextEditの起動
        keymap_global[ "Fn-E" ] = keymap.SubProcessCallCommand( [ "open", "-a", "TextEdit" ], cwd=os.environ["HOME"] )

        # Fn-L : lsコマンドの実行
        keymap_global[ "Fn-L" ] = keymap.SubProcessCallCommand( [ "ls", "-al" ], cwd=os.environ["HOME"] )


    # Fn-S : サブスレッド処理のテスト
    if 1:
        def command_JobTest():

            # サブスレッドで呼ばれる処理
            def jobTest(job_item):
                subprocess.call([ "open", "-a", "Notes" ])

            # サブスレッド処理が完了した後にメインスレッドで呼ばれる処理
            def jobTestFinished(job_item):
                print( "Done." )

            job_item = JobItem( jobTest, jobTestFinished )
            JobQueue.defaultQueue().enqueue(job_item)

        keymap_global[ "Fn-N" ] = command_JobTest


    # --------------------------------------------------------------------
    # クリップボード履歴機能のカスタマイズ

    # クリップボード履歴
    if 1:
        keymap_global[ "Fn-Z"       ] = keymap.command_ClipboardList      # クリップボード履歴表示
        keymap_global[ "Fn-X"       ] = keymap.command_ClipboardRotate    # 直近の履歴を末尾に回す
        keymap_global[ "Fn-Shift-X" ] = keymap.command_ClipboardRemove    # 直近の履歴を削除
        keymap.quote_mark = "> "                                          # 引用貼り付け時の記号

        # クリップボード履歴の最大数 (デフォルト:1000)
        keymap.clipboard_history.maxnum = 1000

        # クリップボード履歴として保存する合計最大サイズ (デフォルト:10MB)
        keymap.clipboard_history.quota = 10*1024*1024


    # クリップボード履歴リスト表示のカスタマイズ
    if 1:

        # 定型文
        fixed_items = [
            ( "name@server.net",     "name@server.net" ),
            ( "住所",                "〒東京都品川区123-456" ),
            ( "電話番号",            "03-4567-8901" ),
        ]

        # フォーマット文字列で現在日時の文字列を生成
        def dateAndTime(fmt):
            def _dateAndTime():
                return datetime.datetime.now().strftime(fmt)
            return _dateAndTime

        # 日時
        datetime_items = [
            ( "YYYY/MM/DD HH:MM:SS",   dateAndTime("%Y/%m/%d %H:%M:%S") ),
            ( "YYYY/MM/DD",            dateAndTime("%Y/%m/%d") ),
            ( "HH:MM:SS",              dateAndTime("%H:%M:%S") ),
            ( "YYYYMMDD_HHMMSS",       dateAndTime("%Y%m%d_%H%M%S") ),
            ( "YYYYMMDD",              dateAndTime("%Y%m%d") ),
            ( "HHMMSS",                dateAndTime("%H%M%S") ),
        ]

        # 文字列に引用符を付ける
        def quoteClipboardText():
            s = getClipboardText()
            lines = s.splitlines(True)
            s = ""
            for line in lines:
                s += keymap.quote_mark + line
            return s

        # 文字列をインデントする
        def indentClipboardText():
            s = getClipboardText()
            lines = s.splitlines(True)
            s = ""
            for line in lines:
                if line.lstrip():
                    line = " " * 4 + line
                s += line
            return s

        # 文字列をアンインデントする
        def unindentClipboardText():
            s = getClipboardText()
            lines = s.splitlines(True)
            s = ""
            for line in lines:
                for i in range(4+1):
                    if i>=len(line) : break
                    if line[i]=='\t':
                        i+=1
                        break
                    if line[i]!=' ':
                        break
                s += line[i:]
            return s

        full_width_chars = "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ！”＃＄％＆’（）＊＋，−．／：；＜＝＞？＠［￥］＾＿‘｛｜｝～０１２３４５６７８９　"
        half_width_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!\"#$%&'()*+,-./:;<=>?@[\]^_`{|}～0123456789 "

        # 文字列を半角文字にする
        def toHalfWidthClipboardText():
            s = getClipboardText()
            s = s.translate(str.maketrans(full_width_chars,half_width_chars))
            return s

        # 文字列を全角文字にする
        def toFullWidthClipboardText():
            s = getClipboardText()
            s = s.translate(str.maketrans(half_width_chars,full_width_chars))
            return s

        # クリップボードの内容をデスクトップに保存
        def command_SaveClipboardToDesktop():

            text = getClipboardText()
            if not text: return

            # utf-8 / CR-LF に変換
            utf8_bom = b"\xEF\xBB\xBF"
            text = text.replace("\r\n","\n")
            text = text.replace("\r","\n")
            text = text.replace("\n","\r\n")
            text = text.encode( encoding="utf-8" )

            # デスクトップに保存
            fullpath = os.path.join( getDesktopPath(), datetime.datetime.now().strftime("clip_%Y%m%d_%H%M%S.txt") )
            fd = open( fullpath, "wb" )
            fd.write(utf8_bom)
            fd.write(text)
            fd.close()

            # テキストエディタを開く
            keymap.editTextFile(fullpath)

        # その他
        other_items = [
            ( "Quote clipboard",            quoteClipboardText ),
            ( "Indent clipboard",           indentClipboardText ),
            ( "Unindent clipboard",         unindentClipboardText ),
            ( "",                           None ),
            ( "To Half-Width",              toHalfWidthClipboardText ),
            ( "To Full-Width",              toFullWidthClipboardText ),
            ( "",                           None ),
            ( "Save clipboard to Desktop",  command_SaveClipboardToDesktop ),
            ( "",                           None ),
            ( "Edit config.py",             keymap.command_EditConfig ),
            ( "Reload config.py",           keymap.command_ReloadConfig ),
        ]

        # クリップボード履歴リストのメニューリスト
        keymap.cblisters += [
            ( "定型文",  cblister_FixedPhrase(fixed_items) ),
            ( "日時",    cblister_FixedPhrase(datetime_items) ),
            ( "その他",  cblister_FixedPhrase(other_items) ),
        ]

