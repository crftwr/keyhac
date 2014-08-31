import sys
import os
import getopt
import shutil
import locale

os.environ["PATH"] = os.path.join( os.path.split(sys.argv[0])[0], 'lib' ) + ";" + os.environ["PATH"]

sys.path[0:0] = [
    os.path.join( os.path.split(sys.argv[0])[0], '..' ),
    os.path.join( os.path.split(sys.argv[0])[0], 'extension' ),
    os.path.join( os.path.split(sys.argv[0])[0], 'lib' ),
    ]

import ckit

ckit.setLocale( locale.getdefaultlocale()[0] )

import keyhac_consolewindow
import keyhac_keymap
import keyhac_ini
import keyhac_resource

if ckit.platform()=="win":
    import keyhac_tasktrayicon

#--------------------------------------------------------------------

debug = False
profile = False

option_list, args = getopt.getopt( sys.argv[1:], "dp" )
for option in option_list:
    if option[0]=="-d":
        debug = True
    elif option[0]=="-p":
        profile = True

#--------------------------------------------------------------------

if __name__ == "__main__":

    ckit.registerWindowClass( "keyhac" )
    
    # exeと同じ位置にある設定ファイルを優先する
    if os.path.exists( os.path.join( ckit.getAppExePath(), 'config.py' ) ):
        ckit.setDataPath( ckit.getAppExePath() )
    else:    
        ckit.setDataPath( os.path.join( ckit.getAppDataPath(), keyhac_resource.keyhac_dirname ) )
        if not os.path.exists(ckit.dataPath()):
            os.mkdir(ckit.dataPath())

    default_config_filename = os.path.join( ckit.getAppExePath(), '_config.py' )
    config_filename = os.path.join( ckit.dataPath(), 'config.py' )
    keyhac_ini.ini_filename = os.path.join( ckit.dataPath(), 'keyhac.ini' )

    # config.py がどこにもない場合は作成する
    if not os.path.exists(config_filename) and os.path.exists(default_config_filename):
        shutil.copy( default_config_filename, config_filename )
    
    keyhac_ini.read()

    ckit.setThemeDefault()

    ckit.JobQueue.createDefaultQueue()
    ckit.CronTable.createDefaultCronTable()

    keymap = keyhac_keymap.Keymap( config_filename, debug, profile )
    if ckit.platform()=="win":
        console_window = keyhac_consolewindow.ConsoleWindow( debug )
        task_tray_icon = keyhac_tasktrayicon.TaskTrayIcon( debug )

        keymap.setConsoleWindow(console_window)
        task_tray_icon.setKeymap(keymap)
        task_tray_icon.setConsoleWindow(console_window)

        console_window.registerStdio()

    keymap.configure()
    
    keymap.startup()
    
    keymap.messageLoop()

    if ckit.platform()=="win":
        console_window.unregisterStdio()

    ckit.CronTable.cancelAll()
    ckit.CronTable.joinAll()

    ckit.JobQueue.cancelAll()
    ckit.JobQueue.joinAll()

    if ckit.platform()=="win":
        task_tray_icon.destroy()

    keymap.destroy()

    if ckit.platform()=="win":
        console_window.saveState()
        console_window.destroy()

    keyhac_ini.write()    
