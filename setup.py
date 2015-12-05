#
# Usage 1 : python3.4 setup.py bdist_mac
#    build Keyhac.app directory.
#
# Usage 2 : python3.4 setup.py bdist_dmg
#    build Keyhac.app and archive as dmg file.
#

import sys
import os
from cx_Freeze import setup, Executable

sys.path[0:0] = [
    os.path.join( os.path.split(sys.argv[0])[0], ".." ),
    ]

PIL_dylibs_path = "/Library/Frameworks/Python.framework/Versions/3.4/lib/python3.4/site-packages/PIL/.dylibs/"

buildOptions = dict(
	optimize = 2,
	packages = [], 
	includes = ["keyhac"], 
	excludes = ["tkinter"], 
	bin_includes = [],
	bin_excludes = ["ckitcore.dylib"],
    include_files = [ 
        (PIL_dylibs_path+"libjpeg.9.dylib",".dylibs/libjpeg.9.dylib"),
        (PIL_dylibs_path+"libtiff.5.dylib",".dylibs/libtiff.5.dylib"),
        (PIL_dylibs_path+"libz.1.2.8.dylib",".dylibs/libz.1.2.8.dylib"),
        "dict",
        "extension",
        "license",
        "theme",
        "readme.txt",
        "_config.py",
        #( "doc/html", "doc" ),
    ],
)

macOptions = dict(
	iconfile = "app.icns",
	bundle_name = "Keyhac",
	custom_info_plist = "Info.plist",
)

dmgOptions = dict(
	volume_label = "Keyhac",
	#applications_shortcut = True,
)

base = None

executables = [
    Executable('keyhac_main.py', base=base, targetName = 'Keyhac')
]

setup(name='Keyhac',
      version = '1.00',
      description = 'Keyhac : Python based keyboard customization tool.',
      options = dict(build_exe = buildOptions, bdist_mac = macOptions, bdist_dmg = dmgOptions ),
      executables = executables)
