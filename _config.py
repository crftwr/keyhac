import sys
import os
import datetime

from keyhac import *

def configure(keymap):

    # --------------------------------------------------------------------
    # config.py編集用のテキストエディタの設定

    # プログラムのファイルパスを設定 (単純な使用方法)
    if 1:
        keymap.editor = "notepad.exe"

    # 呼び出し可能オブジェクトを設定 (高度な使用方法)
    if 0:
        def editor(path):
            shellExecute( None, "notepad.exe", '"%s"'% path, "" )
        keymap.editor = editor

    # --------------------------------------------------------------------
    # 表示のカスタマイズ

    # フォントの設定
    keymap.setFont( "Osaka-Mono", 16 )

    # テーマの設定
    keymap.setTheme("black")

    # --------------------------------------------------------------------

    # キーの単純な置き換え
    #keymap.replaceKey( "LWin", 235 )
    #keymap.replaceKey( "RWin", 255 )

    # ユーザモディファイアキーの定義
    keymap.defineModifier( 235, "User0" )

    # どのウインドウにフォーカスがあっても効くキーマップ
    if 1:
        keymap_global = keymap.defineWindowKeymap()

        # USER0-↑↓←→ : 10pixel単位のウインドウの移動
        keymap_global[ "User0-Left"  ] = keymap.command_MoveWindow( -10, 0 )
        keymap_global[ "User0-Right" ] = keymap.command_MoveWindow( +10, 0 )
        keymap_global[ "User0-Up"    ] = keymap.command_MoveWindow( 0, -10 )
        keymap_global[ "User0-Down"  ] = keymap.command_MoveWindow( 0, +10 )

        # USER0-Shift-↑↓←→ : 1pixel単位のウインドウの移動
        keymap_global[ "User0-Shift-Left"  ] = keymap.command_MoveWindow( -1, 0 )
        keymap_global[ "User0-Shift-Right" ] = keymap.command_MoveWindow( +1, 0 )
        keymap_global[ "User0-Shift-Up"    ] = keymap.command_MoveWindow( 0, -1 )
        keymap_global[ "User0-Shift-Down"  ] = keymap.command_MoveWindow( 0, +1 )

        # USER0-Ctrl-↑↓←→ : 画面の端まで移動
        keymap_global[ "User0-Ctrl-Left"  ] = keymap.command_MoveWindow_MonitorEdge(0)
        keymap_global[ "User0-Ctrl-Right" ] = keymap.command_MoveWindow_MonitorEdge(2)
        keymap_global[ "User0-Ctrl-Up"    ] = keymap.command_MoveWindow_MonitorEdge(1)
        keymap_global[ "User0-Ctrl-Down"  ] = keymap.command_MoveWindow_MonitorEdge(3)

        # クリップボード履歴
        keymap_global[ "Ctrl-Shift-Z"   ] = keymap.command_ClipboardList        # クリップボード履歴表示
        keymap_global[ "Ctrl-Shift-X"   ] = keymap.command_ClipboardRotate      # 直近の履歴を末尾に回す
        keymap_global[ "Ctrl-Shift-Alt-X" ] = keymap.command_ClipboardRemove    # 直近の履歴を削除
        keymap.quote_mark = "> "                                                # 引用貼り付け時の記号

        # キーボードマクロ
        keymap_global[ "User0-0" ] = keymap.command_RecordToggle
        keymap_global[ "User0-1" ] = keymap.command_RecordStart
        keymap_global[ "User0-2" ] = keymap.command_RecordStop
        keymap_global[ "User0-3" ] = keymap.command_RecordPlay
        keymap_global[ "User0-4" ] = keymap.command_RecordClear


    # USER0-F1 : アプリケーションの起動テスト
    if 1:
        keymap_global[ "User0-F1" ] = keymap.command_ShellExecute( None, "notepad.exe", "", "" )


    # USER0-F2 : サブスレッド処理のテスト
    if 1:
        def command_JobTest():

            def jobTest(job_item):
                shellExecute( None, "notepad.exe", "", "" )

            def jobTestFinished(job_item):
                print( "Done." )

            job_item = JobItem( jobTest, jobTestFinished )
            JobQueue.defaultQueue().enqueue(job_item)

        keymap_global[ "User0-F2" ] = command_JobTest


    # Cron (定期的なサブスレッド処理) のテスト
    if 0:
        def cronPing(cron_item):
            os.system( "ping -n 3 www.google.com" )

        cron_item = CronItem( cronPing, 3.0 )
        CronTable.defaultCronTable().add(cron_item)


    # USER0-F : ウインドウのアクティブ化
    if 1:
        keymap_global[ "User0-F" ] = keymap.command_ActivateWindow( "cfiler.exe", "CfilerWindowClass" )


    # USER0-E : アクティブ化するか、まだであれば起動する
    if 1:
        def command_ActivateOrExecuteNotepad():
            wnd = Window.find( "Notepad", None )
            if wnd:
                if wnd.isMinimized():
                    wnd.restore()
                wnd = wnd.getLastActivePopup()
                wnd.setForeground()
            else:
                executeFunc = keymap.command_ShellExecute( None, "notepad.exe", "", "" )
                executeFunc()

        keymap_global[ "User0-E" ] = command_ActivateOrExecuteNotepad


    # Ctrl-Tab で、コンソール関係のウインドウを切り替え
    if 0:

        def isConsoleWindow(wnd):
            if wnd.getClassName() in ("PuTTY","MinTTY","CkwWindowClass"):
                return True
            return False

        keymap_console = keymap.defineWindowKeymap( check_func=isConsoleWindow )

        def command_SwitchConsole():

            root = pyauto.Window.getDesktop()
            last_console = None

            wnd = root.getFirstChild()
            while wnd:
                if isConsoleWindow(wnd):
                    last_console = wnd
                wnd = wnd.getNext()

            if last_console:
                last_console.setForeground()

        keymap_console[ "Ctrl-TAB" ] = command_SwitchConsole


    # USER0-Space : カスタムのリスト表示をつかったアプリケーション起動
    if 1:
        def command_PopApplicationList():

            # すでにリストが開いていたら閉じるだけ
            if keymap.isListWindowOpened():
                keymap.cancelListWindow()
                return

            def popApplicationList():

                applications = [
                    ( "Notepad", keymap.command_ShellExecute( None, "notepad.exe", "", "" ) ),
                    ( "Paint", keymap.command_ShellExecute( None, "mspaint.exe", "", "" ) ),
                ]

                websites = [
                    ( "Google", keymap.command_ShellExecute( None, "https://www.google.co.jp/", "", "" ) ),
                    ( "Facebook", keymap.command_ShellExecute( None, "https://www.facebook.com/", "", "" ) ),
                    ( "Twitter", keymap.command_ShellExecute( None, "https://twitter.com/", "", "" ) ),
                ]

                listers = [
                    ( "App",     cblister_FixedPhrase(applications) ),
                    ( "WebSite", cblister_FixedPhrase(websites) ),
                ]

                item, mod = keymap.popListWindow(listers)

                if item:
                    item[1]()

            # キーフックの中で時間のかかる処理を実行できないので、delayedCall() をつかって遅延実行する
            keymap.delayedCall( popApplicationList, 0 )

        keymap_global[ "User0-Space" ] = command_PopApplicationList


    # USER0-Alt-↑↓←→/Space/PageUp/PageDown : キーボードで擬似マウス操作
    if 1:
        keymap_global[ "User0-Alt-Left"  ] = keymap.command_MouseMove(-10,0)
        keymap_global[ "User0-Alt-Right" ] = keymap.command_MouseMove(10,0)
        keymap_global[ "User0-Alt-Up"    ] = keymap.command_MouseMove(0,-10)
        keymap_global[ "User0-Alt-Down"  ] = keymap.command_MouseMove(0,10)
        keymap_global[ "D-User0-Alt-Space" ] = keymap.command_MouseButtonDown('left')
        keymap_global[ "U-User0-Alt-Space" ] = keymap.command_MouseButtonUp('left')
        keymap_global[ "User0-Alt-PageUp" ] = keymap.command_MouseWheel(1.0)
        keymap_global[ "User0-Alt-PageDown" ] = keymap.command_MouseWheel(-1.0)
        keymap_global[ "User0-Alt-Home" ] = keymap.command_MouseHorizontalWheel(-1.0)
        keymap_global[ "User0-Alt-End" ] = keymap.command_MouseHorizontalWheel(1.0)


    # sendMessageでシステムコマンドを実行
    if 1:
        def close():
            wnd = keymap.getTopLevelWindow()
            wnd.sendMessage( WM_SYSCOMMAND, SC_CLOSE )

        def screenSaver():
            wnd = keymap.getTopLevelWindow()
            wnd.sendMessage( WM_SYSCOMMAND, SC_SCREENSAVE )

        keymap_global[ "User0-C" ] = close              # ウインドウを閉じる
        keymap_global[ "User0-S" ] = screenSaver        # スクリーンセーバー


    # 文字入力のテスト
    if 1:
        keymap_global[ "User0-H" ] = keymap.command_InputText( "Hello / こんにちは" )


    # Editボックスで、Ctrl-Dを削除に当てるなど
    if 0:
        keymap_edit = keymap.defineWindowKeymap( class_name="Edit" )

        keymap_edit[ "Ctrl-D" ] = "Delete"              # 削除
        keymap_edit[ "Ctrl-H" ] = "Back"                # バックスペース
        keymap_edit[ "Ctrl-K" ] = "Shift-End","Ctrl-X"  # 行末まで切り取り


    # メモ帳を Emacs 風にカスタマイズする
    # keymap_edit の条件と重複するため、keymap_editの設定と混ざって機能する。
    if 0:
        keymap_notepad = keymap.defineWindowKeymap( exe_name="notepad.exe", class_name="Edit" )

        # Ctrl-X を マルチストロークの1段目として登録
        keymap_notepad[ "Ctrl-X" ] = keymap.defineMultiStrokeKeymap("Ctrl-X")

        keymap_notepad[ "Ctrl-P" ] = "Up"                           # カーソル上
        keymap_notepad[ "Ctrl-N" ] = "Down"                         # カーソル下
        keymap_notepad[ "Ctrl-F" ] = "Right"                        # カーソル右
        keymap_notepad[ "Ctrl-B" ] = "Left"                         # カーソル左
        keymap_notepad[ "Ctrl-A" ] = "Home"                         # 行の先頭
        keymap_notepad[ "Ctrl-E" ] = "End"                          # 行の末尾
        keymap_notepad[ "Alt-F" ] = "Ctrl-Right"                    # 単語右
        keymap_notepad[ "Alt-B" ] = "Ctrl-Left"                     # 単語左
        keymap_notepad[ "Ctrl-V" ] = "PageDown"                     # ページ下
        keymap_notepad[ "Alt-V" ] = "PageUp"                        # ページ上
        keymap_notepad[ "Alt-Comma" ] = "Ctrl-Home"                 # バッファ先頭
        keymap_notepad[ "Alt-Period" ] = "Ctrl-End"                 # バッファ末尾
        keymap_notepad[ "Ctrl-X" ][ "Ctrl-F" ] = "Ctrl-O"           # ファイルを開く
        keymap_notepad[ "Ctrl-X" ][ "Ctrl-S" ] = "Ctrl-S"           # 保存
        keymap_notepad[ "Ctrl-X" ][ "Ctrl-W" ] = "Alt-F","Alt-A"    # 名前を付けて保存
        keymap_notepad[ "Ctrl-X" ][ "U" ] = "Ctrl-Z"                # アンドゥ
        keymap_notepad[ "Ctrl-S" ] = "Ctrl-F"                       # 検索
        keymap_notepad[ "Alt-X" ] = "Ctrl-G"                        # 指定行へ移動
        keymap_notepad[ "Ctrl-X" ][ "H" ] = "Ctrl-A"                # 全て選択
        keymap_notepad[ "Ctrl-W" ] = "Ctrl-X"                       # 切り取り
        keymap_notepad[ "Alt-W" ] = "Ctrl-C"                        # コピー
        keymap_notepad[ "Ctrl-Y" ] = "Ctrl-V"                       # 貼り付け
        keymap_notepad[ "Ctrl-X" ][ "Ctrl-C" ] = "Alt-F4"           # 終了

    # Macでのテスト
    if 1:
        keymap_global[ "Ctrl-Z" ] = "Cmd-Z"
        keymap_global[ "Ctrl-X" ] = "Cmd-X"
        keymap_global[ "Ctrl-C" ] = "Cmd-C"
        keymap_global[ "Ctrl-V" ] = "Cmd-V"

        keymap_xcode = keymap.defineWindowKeymap( exe_name="com.apple.dt.Xcode" )
        keymap_xcode[ "Ctrl-F" ] = "Cmd-F"
        

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

