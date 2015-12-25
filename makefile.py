import os
import sys
import subprocess
import shutil
import zipfile
import hashlib

sys.path[0:0] = [
    os.path.join( os.path.split(sys.argv[0])[0], '..' ),
    ]

import keyhac_resource

# makeoption.py というファイルを作れば、
# Python、Svn、Doxygen のインストールディレクトリ等をカスタマイズできる。

try:
    import makeoption
except:
    makeoption = {}

if hasattr(makeoption,"PYTHON_DIR"):
    PYTHON_DIR = makeoption.PYTHON_DIR
else:
    PYTHON_DIR = "c:/Python34"

PYTHON = PYTHON_DIR + "/python.exe"

if hasattr(makeoption,"SVN_DIR"):
    SVN_DIR = makeoption.SVN_DIR
else:
    SVN_DIR = "c:/Program Files/TortoiseSVN/bin"

if hasattr(makeoption,"DOXYGEN_DIR"):
    DOXYGEN_DIR = makeoption.DOXYGEN_DIR
else:
    DOXYGEN_DIR = "c:/Program Files/doxygen"

DIST_DIR = "dist/keyhac"
VERSION = keyhac_resource.keyhac_version.replace(".","")
ARCHIVE_NAME = "keyhac_%s.zip" % VERSION

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

def createZip( zip_filename, items ):
    z = zipfile.ZipFile( zip_filename, "w", zipfile.ZIP_DEFLATED, True )
    for item in items:
        if os.path.isdir(item):
            for root, dirs, files in os.walk(item):
                for f in files:
                    f = os.path.join(root,f)
                    print( f )
                    z.write(f)
        else:
            print( item )
            z.write(item)
    z.close()

DIST_FILES = [
    "keyhac/keyhac.exe",
    "keyhac/lib",
    "keyhac/python34.dll",
    "keyhac/_config.py",
    "keyhac/readme.txt",
    "keyhac/theme/black",
    "keyhac/theme/white",
    "keyhac/license",
    "keyhac/doc",
    "keyhac/library.zip",
    "keyhac/dict/.keepme",
    "keyhac/extension/.keepme",
    ]

def all():
    doc()
    exe()

def exe():
    subprocess.call( [ PYTHON, "setup.py", "build" ] )

    if 1:
        os.chdir("dist")
        createZip( ARCHIVE_NAME, DIST_FILES )
        os.chdir("..")
    
    fd = open( "dist/%s" % ARCHIVE_NAME, "rb" )
    m = hashlib.md5()
    while 1:
        data = fd.read( 1024 * 1024 )
        if not data: break
        m.update(data)
    fd.close()
    print( "" )
    print( m.hexdigest() )

def clean():
    rmtree("dist")
    rmtree("build")
    rmtree("doc/html")
    unlink( "tags" )

def doc():
    rmtree( "doc/html" )
    makedirs( "doc/obj" )
    makedirs( "doc/html" )

    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "doc/index_ja.txt", "doc/obj/index_ja.html" ] )
    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "--template=tool/rst2html_template.txt", "doc/index_ja.txt", "doc/obj/index_ja.htm_" ] )

    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "doc/index_en.txt", "doc/obj/index_en.html" ] )
    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "--template=tool/rst2html_template.txt", "doc/index_en.txt", "doc/obj/index_en.htm_" ] )

    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "doc/changes.txt", "doc/obj/changes.html" ] )
    subprocess.call( [ PYTHON, "tool/rst2html_pygments.py", "--stylesheet=tool/rst2html_pygments.css", "--template=tool/rst2html_template.txt", "doc/changes.txt", "doc/obj/changes.htm_" ] )

    subprocess.call( [ DOXYGEN_DIR + "/bin/doxygen.exe", "doc/doxyfile" ] )
    shutil.copytree( "doc/image", "doc/html/image", ignore=shutil.ignore_patterns(".svn","*.pdn") )

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

