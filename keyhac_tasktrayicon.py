import os
import sys
import webbrowser

import ckit
import pyauto

import keyhac_hook
import keyhac_misc

class TaskTrayIcon( ckit.TaskTrayIcon ):
    
    def __init__( self, debug ):
    
        ckit.TaskTrayIcon.__init__( self,
            title = "keyhac",
            lbuttondown_handler = self._onLeftButtonDown,
            lbuttonup_handler = self._onLeftButtonUp,
            rbuttondown_handler = self._onRightButtonDown,
            rbuttonup_handler = self._onRightButtonUp,
            lbuttondoubleclick_handler = self._onLeftButtonDoubleClick,
            )
        
        self.debug = debug
        self.keymap = None
        self.console_window = None
    
    def setKeymap( self, keymap ):
        self.keymap = keymap

    def setConsoleWindow( self, console_window ):
        self.console_window = console_window

    def _onLeftButtonDown( self, x, y, mod ):
        pass

    def _onLeftButtonUp( self, x, y, mod ):
        self.console_window.show(True,True)
        self.console_window.restore()
        self.console_window.foreground()

    def _onRightButtonDown( self, x, y, mod ):
        pass

    def _onRightButtonUp( self, x, y, mod ):

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

        def onClearConsole(info):
            self.console_window.clearLog()

        def onHelp(info):
        
            def jobHelp(job_item):
                help_path = os.path.join( ckit.getAppExePath(), 'doc\\index.html' )
                pyauto.shellExecute( None, help_path, "", "" )

            def jobHelpFinished(job_item):
                print( "Helpを開きました" )
                print( "" )
            
            job_item = ckit.JobItem( jobHelp, jobHelpFinished )
            ckit.JobQueue.defaultQueue().enqueue(job_item)
        
        def onExit(info):
            keyhac_hook.hook.destroy()
            self.console_window.quit()
        
        menu_items = []
        
        menu_items.append( ( "設定のリロード(&R)", onReload ) )

        menu_items.append( ( "設定の編集(&E)", onEdit ) )
        
        if self.debug:
            menu_items.append( ( "内部ログ OFF(&D)", onDebugOff ) )
        else:
            menu_items.append( ( "内部ログ ON(&D)", onDebugOn ) )

        menu_items.append( ( "-", None ) )
        
        if self.keymap.hook_enabled:
            menu_items.append( ( "フック OFF(&K)", onHookOff ) )
        else:
            menu_items.append( ( "フック ON(&K)", onHookOn ) )

        menu_items.append( ( "-", None ) )

        if self.keymap.record_status in ( None, "recorded" ):
            menu_items.append( ( "キー操作 記録開始(&S)", onRecordStart ) )
        elif self.keymap.record_status in ( "recording", ):
            menu_items.append( ( "キー操作 記録終了(&S)", onRecordStop ) )

        menu_items.append( ( "-", None ) )

        menu_items.append( ( "端末のクリア(&C)", onClearConsole ) )
            
        menu_items.append( ( "-", None ) )

        menu_items.append( ( "ヘルプ(&H)", onHelp ) )

        menu_items.append( ( "-", None ) )

        menu_items.append( ( "keyhacの終了(&X)", onExit ) )

        x, y = pyauto.Input.getCursorPos()
        self.popupMenu( x, y, menu_items )

    def _onLeftButtonDoubleClick( self, x, y, mod ):
        pass

