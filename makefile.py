import os
import sys
import subprocess
import shutil

sys.path[0:0] = [
    os.path.join( os.path.split(sys.argv[0])[0], '..' ),
    ]

import keyhac_resource

PYTHON = "python3.4"
DOXYGEN = "/Applications/Doxygen.app/Contents/Resources/doxygen"

def unlink(filename):
    try:
        os.unlink(filename)
    except OSError:
        pass

def makedirs(dirname):
    try:
        os.makedirs(dirname)
    except OSError:
        pass

def rmtree(dirname):
    try:
        shutil.rmtree(dirname)
    except OSError:
        pass

def clean():
    rmtree("dist")
    rmtree("build")
    rmtree("doc/html_en")
    rmtree("doc/html_ja")
    unlink( "tags" )

def all():
    doc()
    dmg()

def dmg():
    subprocess.call( [ PYTHON, "setup.py", "bdist_dmg" ] )

def doc():
    rmtree("doc/html_en")
    rmtree("doc/html_ja")
    makedirs( "doc/obj" )
    makedirs( "doc/html_en" )
    makedirs( "doc/html_ja" )

    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "doc/index_en.txt", "doc/obj/index_en.html" ] )
    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "--template=tool/rst2html_template.txt", "doc/index_en.txt", "doc/obj/index_en.htm_" ] )

    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "doc/index_ja.txt", "doc/obj/index_ja.html" ] )
    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "--template=tool/rst2html_template.txt", "doc/index_ja.txt", "doc/obj/index_ja.htm_" ] )

    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "doc/changes.txt", "doc/obj/changes.html" ] )
    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "--template=tool/rst2html_template.txt", "doc/changes.txt", "doc/obj/changes.htm_" ] )

    subprocess.call( [ DOXYGEN, "doc/doxyfile_en" ] )
    subprocess.call( [ DOXYGEN, "doc/doxyfile_ja" ] )
    shutil.copytree( "doc/image", "doc/html_en/image", ignore=shutil.ignore_patterns(".svn","*.pdn") )
    shutil.copytree( "doc/image", "doc/html_ja/image", ignore=shutil.ignore_patterns(".svn","*.pdn") )

def run():
    subprocess.call( [ PYTHON, "keyhac_main.py" ] )

def debug():
    subprocess.call( [ PYTHON, "keyhac_main.py", "-d" ] )

def profile():
    subprocess.call( [ PYTHON, "keyhac_main.py", "-d", "-p" ] )

if len(sys.argv)<=1:
    target = "all"
else:
    target = sys.argv[1]

eval( target + "()" )
