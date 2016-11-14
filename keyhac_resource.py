import ckit

keyhac_appname = "Keyhac"
keyhac_dirname = "Keyhac"
keyhac_version = "1.76"

_startup_string_fmt = """\
%s version %s:
  http://sites.google.com/site/craftware/
"""

def startupString():
    return _startup_string_fmt % ( keyhac_appname, keyhac_version )


ckit.strings.setString( "log_internal_log_output_enabled",
    en_US = "Enabled the internal log output.",
    ja_JP = "内部ログの出力を ON にしました." )
ckit.strings.setString( "log_internal_log_output_disabled",
    en_US = "Disabled the internal log output.",
    ja_JP = "内部ログの出力を OFF にしました." )
ckit.strings.setString( "log_hook_enabled",
    en_US = "Enabled the hook.",
    ja_JP = "フックを ON にしました." )
ckit.strings.setString( "log_hook_disabled",
    en_US = "Disabled the hook.",
    ja_JP = "フックを OFF にしました." )
ckit.strings.setString( "log_help_opened",
    en_US = "Opened the help document.",
    ja_JP = "Helpを開きました." )
ckit.strings.setString( "log_macro_recording_started",
    en_US = "Keyboard macro : Recording started",
    ja_JP = "キーボードマクロ : 記録開始" )
ckit.strings.setString( "log_macro_recording_stopped",
    en_US = "Keyboard macro : Recording stopped",
    ja_JP = "キーボードマクロ : 記録終了" )
ckit.strings.setString( "log_macro_recording_cleared",
    en_US = "Keyboard macro : Cleared",
    ja_JP = "キーボードマクロ : 消去" )
ckit.strings.setString( "log_macro_replay",
    en_US = "Keyboard macro : Replay",
    ja_JP = "キーボードマクロ : 再生" )
ckit.strings.setString( "log_config_reloaded",
    en_US = "Reloaded the config file.",
    ja_JP = "設定ファイルをリロードしました." )
ckit.strings.setString( "log_config_editor_launched",
    en_US = "Launched the editor of config file.",
    ja_JP = "設定ファイルのエディタを起動しました." )

ckit.strings.setString( "log_key_hook_force_cancellation_detected",
    en_US = (
        "-----------------------------------------\n"
        "Detected the force cancellation of the key hook.\n"
        "Will re-install the hook automatically.\n"
        "\n"
        "If you see this key hook force cancellation often, please check if you are\n"
        "not calling time consuming procedure (more than 300msec) in the main-thread.\n"
        "And move the time consuming procedure to sub-thread using JobQueue/JobItem.\n"
        "-----------------------------------------" ),
    ja_JP = (
        "-----------------------------------------\n"
        "キーフック強制解除を検出しました.\n"
        "自動的にフックの再設定を行います.\n"
        "\n"
        "キーフックの強制解除が頻発する場合、時間のかかる処理(300ミリ秒以上)が\n"
        "メインスレッドで呼び出されていないかを、確認してください.\n"
        "時間のかかる処理は JobQueue/JobItem を使ってサブスレッドに追い出してください.\n"
        "-----------------------------------------" )
    )

ckit.strings.setString( "log_clipboard_content_mismatch_detected",
    en_US = (
        "---------------------------------------\n"
        "Detected clipboard contents mismatch.\n"
        "Re-installed the clipboard hook.\n"
        "---------------------------------------" ),
    ja_JP = (
        "---------------------------------------------\n"
        "クリップボードの内容の不一致を検出しました.\n"
        "クリップボードのフックを再設定しました.\n"
        "---------------------------------------------" )
    )


ckit.strings.setString( "balloon_macro_recording_started",
    en_US = "Recording started",
    ja_JP = "記録開始" )
ckit.strings.setString( "balloon_macro_recording_stopped",
    en_US = "Recording stopped",
    ja_JP = "記録終了" )
ckit.strings.setString( "balloon_macro_recording_cleared",
    en_US = "Cleared",
    ja_JP = "消去" )


ckit.strings.setString( "error_invalid_key_expression",
    en_US = "ERROR : Invalid key expression :",
    ja_JP = "ERROR : キーの表記が正しくありません :" )
ckit.strings.setString( "error_macro_too_long",
    en_US = "ERROR : Keyboard macro is too long.",
    ja_JP = "ERROR : キーボードマクロが長すぎます." )
ckit.strings.setString( "error_unexpected",
    en_US = "ERROR : Unexpected error happened :",
    ja_JP = "ERROR : 予期しないエラーが発生しました :" )
ckit.strings.setString( "error_invalid_expression_for_argument",
    en_US = "ERROR : Invalid expression for argument '%s' :",
    ja_JP = "ERROR : 引数 %s の表記が正しくありません :" )
ckit.strings.setString( "error_already_defined_as_modifier",
    en_US = "ERROR : Already defined as a modifier :",
    ja_JP = "ERROR : すでにモディファイアキーとして定義されています :" )
ckit.strings.setString( "error_invalid_mouse_button_expression",
    en_US = "ERROR : Invalid mouse button expression :",
    ja_JP = "ERROR : マウスボタンの表記が正しくありません :" )
ckit.strings.setString( "error_focus_not_found",
    en_US = "ERROR : Focus window not found.",
    ja_JP = "ERROR : フォーカスウインドウがありません." )
ckit.strings.setString( "error_regex",
    en_US = "ERROR : Regular expression error :",
    ja_JP = "ERROR : 正規表現のエラー :" )


ckit.strings.setString( "warning_api_deprecated",
    en_US = 'WARNING : "%s" is deprecated. Use "%s" instead.',
    ja_JP = 'WARNING : "%s" は廃止予定です. 代わりに "%s" を使ってください.' )


ckit.strings.setString( "menu_reload_config",
    en_US = "&Reload config file",
    ja_JP = "設定のリロード(&R)" )
ckit.strings.setString( "menu_edit_config",
    en_US = "&Edit config file",
    ja_JP = "設定の編集(&E)" )
ckit.strings.setString( "menu_internal_log_output_enable",
    en_US = "Enable the &Internal log output",
    ja_JP = "内部ログをON(&I)" )
ckit.strings.setString( "menu_internal_log_output_disable",
    en_US = "Disable the &Internal log output",
    ja_JP = "内部ログをOFF(&I)" )
ckit.strings.setString( "menu_hook_enable",
    en_US = "Enable the Hoo&k",
    ja_JP = "フックをON(&K)" )
ckit.strings.setString( "menu_hook_disable",
    en_US = "Disable the Hoo&k",
    ja_JP = "フックをOFF(&K)" )
ckit.strings.setString( "menu_start_recording",
    en_US = "&Start recording key input",
    ja_JP = "キー操作 記録開始(&S)" )
ckit.strings.setString( "menu_stop_recording",
    en_US = "&Stop recording key input",
    ja_JP = "キー操作 記録終了(&S)" )
ckit.strings.setString( "menu_clear_console",
    en_US = "&Clear console",
    ja_JP = "端末のクリア(&C)" )
ckit.strings.setString( "menu_help",
    en_US = "&Help",
    ja_JP = "ヘルプ(&H)" )
ckit.strings.setString( "menu_exit",
    en_US = "E&xit",
    ja_JP = "Keyhacの終了(&X)" )


ckit.strings.setString( "title_clipboard",
    en_US = "Clipboard",
    ja_JP = "クリップボード" )

ckit.strings.setString( "msgbox_cant_create_multiple_hook",
    en_US = "Can't create multiple Hook objects.\n",
    ja_JP = "複数の Hook オブジェクトを作成することはできません.\n" )


