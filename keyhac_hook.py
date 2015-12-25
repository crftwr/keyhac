import sys
import ctypes

import pyauto

import ckit

import keyhac_resource

try:
    hook = pyauto.Hook()
except ValueError:
    ctypes.windll.user32.MessageBoxW( 0, ckit.strings["msgbox_cant_create_multiple_hook"], keyhac_resource.keyhac_appname, 0 )
    sys.exit(0)
