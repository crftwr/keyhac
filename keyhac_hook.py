import sys
import ctypes

import ckit

import keyhac_resource

try:
    hook = ckit.Hook()
except ValueError:
    message = "Can't create multiple Hook objects.\n"
    ctypes.windll.user32.MessageBoxW( 0, message, keyhac_resource.keyhac_appname, 0 )
    sys.exit(0)
