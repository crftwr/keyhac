import os
import sys
import subprocess

import ckit

import keyhac_hook
import keyhac_misc

class TaskTrayIcon( ckit.TaskTrayIcon ):

    def __init__( self, debug ):

        menu = self.createMenu()

        ckit.TaskTrayIcon.__init__( self,
            icon = ckit.getThemeAssetFullpath("icon.png"),
            menu = menu
            )

        self.debug = debug
        self.keymap = None
        self.console_window = None

    def setKeymap( self, keymap ):
        self.keymap = keymap

    def setConsoleWindow( self, console_window ):
        self.console_window = console_window

    def _onLeftButtonUp( self, x, y, mod ):
        self.console_window.show(True,True)
        self.console_window.restore()
        self.console_window.foreground()

    def createMenu(self):

        def onReload(info):
            self.keymap.command_ReloadConfig()

        def onEdit(info):
            self.keymap.command_EditConfig()

        def onDebugOn(info):
            self.debug = True
            self.keymap.enableDebug(self.debug)
            print( ckit.strings["log_internal_log_output_enabled"] )
            print( "" )

        def onDebugOff(info):
            self.debug = False
            self.keymap.enableDebug(self.debug)
            print( ckit.strings["log_internal_log_output_disabled"] )
            print( "" )

        def onHookOn(info):
            self.keymap.enableHook(True)
            print( ckit.strings["log_hook_enabled"] )
            print( "" )

        def onHookOff(info):
            self.keymap.enableHook(False)
            print( ckit.strings["log_hook_disabled"] )
            print( "" )

        def onRecordStart(info):
            self.keymap.command_RecordStart()

        def onRecordStop(info):
            self.keymap.command_RecordStop()

        def onShowLog(info):
            self.console_window.show(True,True)
            self.console_window.restore()
            self.console_window.foreground()

        def onClearLog(info):
            self.console_window.clearLog()

        def onHelp(info):

            def jobHelp(job_item):

                if ckit.strings.locale == ckit.TranslatedStrings.ja_JP:
                    dirname = "ja"
                else:
                    dirname = "en"

                help_path = os.path.join( ckit.getAppExePath(), 'doc/%s/index.html' % dirname )
                subprocess.call( [ "open", help_path ] )

            def jobHelpFinished(job_item):
                print( ckit.strings["log_help_opened"] )
                print( "" )

            job_item = ckit.JobItem( jobHelp, jobHelpFinished )
            ckit.JobQueue.defaultQueue().enqueue(job_item)

        def onExit(info):
            self.keymap.quit()

        menu = ckit.MenuNode( items=[
            ckit.MenuNode( "reload", ckit.strings["menu_reload_config"], onReload ),
            ckit.MenuNode( "edit", ckit.strings["menu_edit_config"], onEdit ),
            ckit.MenuNode( "log", ckit.strings["menu_internal_log_output_disable"], onDebugOff, visible = lambda: self.debug ),
            ckit.MenuNode( "log", ckit.strings["menu_internal_log_output_enable"], onDebugOn, visible = lambda: not self.debug ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "hook", ckit.strings["menu_hook_disable"], onHookOff, visible = lambda: self.keymap.hook_enabled ),
            ckit.MenuNode( "hook", ckit.strings["menu_hook_enable"], onHookOn, visible = lambda: not self.keymap.hook_enabled ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "record", ckit.strings["menu_start_recording"], onRecordStart, visible = lambda: self.keymap.record_status in ( None, "recorded" ) ),
            ckit.MenuNode( "record", ckit.strings["menu_stop_recording"], onRecordStop, visible = lambda: self.keymap.record_status in ( "recording", ) ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "show_log", ckit.strings["menu_show_console"], onShowLog ),
            ckit.MenuNode( "clear_log", ckit.strings["menu_clear_console"], onClearLog ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "help", ckit.strings["menu_help"], onHelp ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "exit", ckit.strings["menu_exit"], onExit ),
        ])

        return menu
