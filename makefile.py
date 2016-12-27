import os
import sys
import getopt
import shutil
import zipfile
import hashlib
import subprocess
import py_compile

sys.path[0:0] = [
    os.path.join( os.path.split(sys.argv[0])[0], '..' ),
    ]

import keyhac_resource

#-------------------------------------------

action = "all"

debug = False

option_list, args = getopt.getopt( sys.argv[1:], "d" )
for option in option_list:
    if option[0]=="-d":
        debug = True

if len(args)>0:
    action = args[0]

#-------------------------------------------

PYTHON = "python3"

PYTHON_FRAMEWORK_DIR = "/Library/Frameworks/Python.framework/Versions/Current/"

DOXYGEN = "/Applications/Doxygen.app/Contents/Resources/doxygen"

DIST_DIR = "dist/keyhac"
VERSION = keyhac_resource.keyhac_version.replace(".","")
ARCHIVE_NAME = "keyhac_%s.zip" % VERSION

DIST_FILES = {
    "Keyhac.app/Contents/MacOS/Keyhac"   : "Keyhac.app/Contents/MacOS/Keyhac",
    "Keyhac.app/Contents/Resources"      : "Keyhac.app/Contents/Resources",
    "Keyhac.app/Contents/_CodeSignature" : "Keyhac.app/Contents/_CodeSignature",
    "Keyhac.app/Contents/Info.plist"     : "Keyhac.app/Contents/Info.plist",
    "Keyhac.app/Contents/PkgInfo"        : "Keyhac.app/Contents/PkgInfo",
    "lib" :                 "Keyhac.app/Contents/MacOS/lib",
    "Python" :              "Keyhac.app/Contents/MacOS/Python",
    "_config.py" :          "Keyhac.app/Contents/MacOS/_config.py",
    "readme_en.txt" :       "Keyhac.app/Contents/MacOS/readme_en.txt",
    "readme_ja.txt" :       "Keyhac.app/Contents/MacOS/readme_ja.txt",
    "theme/black" :         "Keyhac.app/Contents/MacOS/theme/black",
    "theme/white" :         "Keyhac.app/Contents/MacOS/theme/white",
    "license" :             "Keyhac.app/Contents/MacOS/license",
    "doc/html_en" :         "Keyhac.app/Contents/MacOS/doc/en",
    "doc/html_ja" :         "Keyhac.app/Contents/MacOS/doc/ja",
    "library.zip" :         "Keyhac.app/Contents/MacOS/library.zip",
    "dict/.keepme" :        "Keyhac.app/Contents/MacOS/dict/.keepme",
    "extension/.keepme" :   "Keyhac.app/Contents/MacOS/extension/.keepme",
    }

#-------------------------------------------

def unlink(filename):
    try:
        os.unlink(filename)
    except (OSError, FileNotFoundError):
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

def compilePythonRecursively( src, dst, file_black_list=[], directory_black_list=[] ):

    for root, dirs, files in os.walk( src ):

        for directory_to_remove in directory_black_list:
            if directory_to_remove in dirs:
                dirs.remove(directory_to_remove)

        for file_to_remove in file_black_list:
            if file_to_remove in files:
                files.remove(file_to_remove)

        for filename in files:
            if filename.endswith(".py"):
                src_filename = os.path.join(root,filename)
                dst_filename = os.path.join(dst+root[len(src):],filename+"c")
                print("compile", src_filename, dst_filename )
                py_compile.compile( src_filename, dst_filename, optimize=2 )


def createZip( zip_filename, items ):
    z = zipfile.ZipFile( zip_filename, "w", zipfile.ZIP_DEFLATED, True )
    for item in items:
        if os.path.isdir(item):
            for root, dirs, files in os.walk(item):
                for f in files:
                    f = os.path.normpath(os.path.join(root,f))
                    print( f )
                    z.write(f)
        else:
            print( item )
            z.write(item)
    z.close()


#-------------------------------------------

def target_all():

    target_compile()
    target_copy()
    target_document()
    target_dist()
    target_archive()


def target_compile():

    # compile python source files
    compilePythonRecursively( PYTHON_FRAMEWORK_DIR + "lib/python3.5", "build/Lib",
        directory_black_list = [
            "site-packages",
            "test",
            "tests",
            "idlelib",
            ]
        )
    compilePythonRecursively( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL", "build/Lib/PIL" )
    compilePythonRecursively( "../ckit", "build/Lib/ckit" )
    compilePythonRecursively( "../pyauto", "build/Lib/pyauto" )
    compilePythonRecursively( ".", "build/Lib",
        file_black_list = [
            "makefile.py",
            "_config.py",
            "config.py",
            ]
        )

    # archive python compiled files
    os.chdir("build/Lib")
    createZip( "../../library.zip", "." )
    os.chdir("../..")


def target_copy():

    unlink("Python")
    rmtree("lib")

    shutil.copy( PYTHON_FRAMEWORK_DIR + "Python", "Python" )

    shutil.copytree( PYTHON_FRAMEWORK_DIR + "lib/python3.5/lib-dynload", "lib/",
        ignore=shutil.ignore_patterns()
        )

    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_imaging.cpython-35m-darwin.so", "lib/_imaging.so" )
    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_imagingcms.cpython-35m-darwin.so", "lib/_imagingcms.so" )
    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_imagingft.cpython-35m-darwin.so", "lib/_imagingft.so" )
    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_imagingmath.cpython-35m-darwin.so", "lib/_imagingmath.so" )
    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_imagingmorph.cpython-35m-darwin.so", "lib/_imagingmorph.so" )
    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_imagingtk.cpython-35m-darwin.so", "lib/_imagingtk.so" )
    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/_webp.cpython-35m-darwin.so", "lib/_webp.so" )

    shutil.copytree( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/PIL/.dylibs", "lib/.dylibs",
                    ignore=shutil.ignore_patterns()
                    )

    shutil.copy( PYTHON_FRAMEWORK_DIR + "lib/python3.5/site-packages/accessibility-0.4.0-py3.5-macosx-10.6-intel.egg/accessibility.cpython-35m-darwin.so", "lib/" )

    shutil.copy( "../ckit/ckitcore.so", "lib/ckitcore.so" )


def target_document():
    rmtree( "doc/html_en" )
    rmtree( "doc/html_ja" )
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
    shutil.copytree( "doc/image", "doc/html_en/image", ignore=shutil.ignore_patterns("*.pdn",) )

    subprocess.call( [ DOXYGEN, "doc/doxyfile_ja" ] )
    shutil.copytree( "doc/image", "doc/html_ja/image", ignore=shutil.ignore_patterns("*.pdn",) )


def target_dist():

    rmtree("dist/Keyhac.app")

    src_root = "."
    dst_root = "./dist"

    for src, dst in DIST_FILES.items():

        src = os.path.join(src_root,src)
        dst = os.path.join(dst_root,dst)

        print( "copy : %s -> %s" % (src,dst) )

        if os.path.isdir(src):
            shutil.copytree( src, dst )
        else:
            makedirs( os.path.dirname(dst) )
            shutil.copy( src, dst )


def target_archive():

    makedirs("dist")

    os.chdir("dist")
    createZip( ARCHIVE_NAME, DIST_FILES.values() )
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


def target_clean():
    rmtree("dist")
    rmtree("build")
    rmtree("doc/html_en")
    rmtree("doc/html_ja")
    unlink( "tags" )


#-------------------------------------------

eval( "target_" + action +"()" )
