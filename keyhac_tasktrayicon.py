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
            print( "内部ログの出力を ON にしました" )
            print( "" )
        
        def onDebugOff(info):
            self.debug = False
            self.keymap.enableDebug(self.debug)
            print( "内部ログの出力を OFF にしました" )
            print( "" )
        
        def onHookOn(info):
            self.keymap.enableHook(True)
            print( "フックを ON にしました" )
            print( "" )
        
        def onHookOff(info):
            self.keymap.enableHook(False)
            print( "フックを OFF にしました" )
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
                #help_path = os.path.join( ckit.getAppExePath(), 'doc/index.html' )
                help_path = "http://crftwr.github.io/keyhac/mac/doc/"
                subprocess.Popen( [ "/usr/bin/open", help_path ] )
            
            def jobHelpFinished(job_item):
                print( "Helpを開きました" )
                print( "" )
            
            job_item = ckit.JobItem( jobHelp, jobHelpFinished )
            ckit.JobQueue.defaultQueue().enqueue(job_item)
        
        def onExit(info):
            self.keymap.quit()
        
        menu = ckit.MenuNode( items=[
            ckit.MenuNode( "reload", "設定のリロード", onReload ),
            ckit.MenuNode( "edit", "設定の編集", onEdit ),
            ckit.MenuNode( "log", "内部ログ OFF", onDebugOff, visible = lambda: self.debug ),
            ckit.MenuNode( "log", "内部ログ ON", onDebugOn, visible = lambda: not self.debug ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "hook", "フック OFF", onHookOff, visible = lambda: self.keymap.hook_enabled ),
            ckit.MenuNode( "hook", "フック ON", onHookOn, visible = lambda: not self.keymap.hook_enabled ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "record", "キー操作 記録開始", onRecordStart, visible = lambda: self.keymap.record_status in ( None, "recorded" ) ),
            ckit.MenuNode( "record", "キー操作 記録終了", onRecordStop, visible = lambda: self.keymap.record_status in ( "recording", ) ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "show_log", "ログの表示", onShowLog ),
            ckit.MenuNode( "clear_log", "ログのクリア", onClearLog ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "help", "ヘルプ", onHelp ),
            ckit.MenuNode( separator=True ),
            ckit.MenuNode( "exit", "keyhacの終了", onExit ),
        ])

        return menu
