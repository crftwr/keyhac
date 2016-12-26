import sys
import os
import getopt
import shutil
import locale

import importlib.abc

class CustomPydFinder(importlib.abc.MetaPathFinder):
    def find_module( self, fullname, path=None ):
        exe_path = os.path.split(sys.argv[0])[0]
        pyd_filename_body = fullname.split(".")[-1]
        pyd_fullpath = os.path.join( exe_path, "lib", pyd_filename_body + ".pyd" )
        print("CustomPydFinder :", pyd_fullpath)
        if os.path.exists(pyd_fullpath):
            for importer in sys.meta_path:
                if isinstance(importer, self.__class__):
                    continue
                loader = importer.find_module( fullname, None)
                if loader:
                    return loader

sys.meta_path.append(CustomPydFinder())

import ckit

import keyhac_consolewindow
import keyhac_tasktrayicon
import keyhac_keymap
import keyhac_ini
import keyhac_resource

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


# アクセシビリティの設定をチェック
if not ckit.Hook.isAllowed(True):
    sys.exit(0)

ckit.registerWindowClass( "Keyhac" )

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

console_window = keyhac_consolewindow.ConsoleWindow( debug )

task_tray_icon = keyhac_tasktrayicon.TaskTrayIcon( debug )

keymap.setConsoleWindow(console_window)

task_tray_icon.setKeymap(keymap)
task_tray_icon.setConsoleWindow(console_window)

console_window.registerStdio()

keymap.configure()

keymap.startup()

keymap.messageLoop()

console_window.unregisterStdio()

ckit.CronTable.cancelAll()
ckit.CronTable.joinAll()

ckit.JobQueue.cancelAll()
ckit.JobQueue.joinAll()

task_tray_icon.destroy()

keymap.destroy()

console_window.saveState()
console_window.destroy()

keyhac_ini.write()
